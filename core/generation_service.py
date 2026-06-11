import time

import httpx
from astrbot.api.event import AstrMessageEvent

try:
    from .config import PluginConfigReader, normalize_base_url
    from .generation import (
        DEFAULT_REFERENCE_PROMPT_EDIT,
        DEFAULT_REFERENCE_PROMPT_SELFIE,
        GenerationAttemptOutcome,
        GenerationMode,
        GenerationResult,
        GenerationRuntimeConfig,
        GenerationTask,
        PreparedGenerationRequest,
        WHITE_REFERENCE_IMAGE_NAME,
        build_payload,
        get_reference_prompt_lines,
        merge_refs,
        resolve_mode,
    )
    from .generation_retry_policy import (
        classify_retry_exception,
        format_retry_notice,
        should_retry_http_error,
        summarize_error_body,
    )
    from .generation_stream_handler import GenerationStreamHandler
    from .media import ImageMediaService
    from .messages import (
        edit_mode_requires_ref,
        generation_request_failed,
        no_generated_image_result,
        plugin_config_required,
        ref_image_unavailable,
        selfie_mode_requires_ref,
        text_mode_rejects_refs,
    )
    from .retry import RetryContext, RetrySignalError, run_with_retries
    from .selfie_refs import SelfieReferenceService
    from .sender import Sender
    from .storage import GeneratedImageStore
except ImportError:
    from core.config import PluginConfigReader, normalize_base_url
    from core.generation import (
        DEFAULT_REFERENCE_PROMPT_EDIT,
        DEFAULT_REFERENCE_PROMPT_SELFIE,
        GenerationAttemptOutcome,
        GenerationMode,
        GenerationResult,
        GenerationRuntimeConfig,
        GenerationTask,
        PreparedGenerationRequest,
        WHITE_REFERENCE_IMAGE_NAME,
        build_payload,
        get_reference_prompt_lines,
        merge_refs,
        resolve_mode,
    )
    from core.generation_retry_policy import (
        classify_retry_exception,
        format_retry_notice,
        should_retry_http_error,
        summarize_error_body,
    )
    from core.generation_stream_handler import GenerationStreamHandler
    from core.media import ImageMediaService
    from core.messages import (
        edit_mode_requires_ref,
        generation_request_failed,
        no_generated_image_result,
        plugin_config_required,
        ref_image_unavailable,
        selfie_mode_requires_ref,
        text_mode_rejects_refs,
    )
    from core.retry import RetryContext, RetrySignalError, run_with_retries
    from core.selfie_refs import SelfieReferenceService
    from core.sender import Sender
    from core.storage import GeneratedImageStore


class GenerationService:
    def __init__(
        self,
        config_reader: PluginConfigReader,
        media_service: ImageMediaService,
        image_store: GeneratedImageStore,
        selfie_ref_service: SelfieReferenceService,
        sender: Sender,
    ):
        self.config_reader = config_reader
        self.media_service = media_service
        self.selfie_ref_service = selfie_ref_service
        self.sender = sender
        self.stream_handler = GenerationStreamHandler(media_service, image_store, sender)

    async def generate_result(
        self,
        event: AstrMessageEvent,
        task: GenerationTask,
    ) -> GenerationResult:
        ref_urls = list(task.ref_urls)

        try:
            runtime_config = self._load_runtime_config()
            request = self._prepare_request(
                task.mode,
                ref_urls,
                self._get_event_refs(event, task.mode),
                use_white_reference_image=runtime_config.use_white_reference_image,
            )
        except ValueError as exc:
            return self.sender.text_result(event, str(exc))

        started_at = time.perf_counter()
        image_error: str | None = None

        try:
            async with httpx.AsyncClient(timeout=runtime_config.timeout) as client:
                try:
                    result = await self._execute_request(
                        client,
                        event,
                        task,
                        request,
                        runtime_config,
                        started_at,
                        image_error_state={"value": image_error},
                    )
                except ValueError as exc:
                    return self.sender.text_result(event, str(exc))
                except RetrySignalError as exc:
                    return self.sender.text_result(event, generation_request_failed(exc.detail), log_level="error")

                image_error = result.image_error
                if result.response is not None:
                    return result.response

            if image_error:
                return self.sender.text_result(event, image_error)
            return self.sender.text_result(event, no_generated_image_result())
        except httpx.HTTPError as exc:
            return self.sender.text_result(event, generation_request_failed(f"{exc}"), log_level="error")

    def _load_runtime_config(self) -> GenerationRuntimeConfig:
        base_url = self.config_reader.get_str("base_url", "")
        api_key = self.config_reader.get_str("api_key", "")
        model = self.config_reader.get_str("model", "")

        if not base_url:
            raise ValueError(plugin_config_required("base_url"))
        if not api_key:
            raise ValueError(plugin_config_required("api_key"))
        if not model:
            raise ValueError(plugin_config_required("model"))

        return GenerationRuntimeConfig(
            base_url=normalize_base_url(base_url),
            api_key=api_key,
            model=model,
            timeout=self.config_reader.get_timeout(),
            generated_image_keep_count=self.config_reader.get_generated_image_keep_count(),
            retry_count=self.config_reader.get_generation_retry_count(),
            send_generated_image_in_chat=self.config_reader.get_bool("send_generated_image_in_chat", False),
            use_white_reference_image=self.config_reader.get_bool("text_mode_use_white_reference_image", False),
        )

    def _get_event_refs(self, event: AstrMessageEvent, mode: GenerationMode) -> list[str]:
        if mode not in {"auto", "edit", "selfie"}:
            return []
        return self.media_service.extract_refs_from_event(event.message_obj, event.message_str)

    def _prepare_request(
        self,
        mode: GenerationMode,
        ref_urls: list[str],
        event_refs: list[str],
        *,
        use_white_reference_image: bool,
    ) -> PreparedGenerationRequest:
        effective_refs = list(ref_urls)
        if mode == "selfie" and not effective_refs and not event_refs:
            effective_refs = self.selfie_ref_service.get_all_selfie_refs()

        if mode == "text" and (effective_refs or event_refs):
            raise ValueError(text_mode_rejects_refs())
        if mode == "edit" and not (effective_refs or event_refs):
            raise ValueError(edit_mode_requires_ref())
        if mode == "selfie" and not (effective_refs or event_refs):
            raise ValueError(selfie_mode_requires_ref())

        merged_refs = merge_refs(effective_refs, event_refs)
        resolved_mode = resolve_mode(mode, merged_refs)
        request_refs = list(merged_refs)
        if resolved_mode == "text" and use_white_reference_image:
            request_refs.append(str(self.media_service.get_white_reference_image_path(WHITE_REFERENCE_IMAGE_NAME)))
        return PreparedGenerationRequest(resolved_mode=resolved_mode, request_refs=request_refs)

    async def _execute_request(
        self,
        client: httpx.AsyncClient,
        event: AstrMessageEvent,
        task: GenerationTask,
        request: PreparedGenerationRequest,
        runtime_config: GenerationRuntimeConfig,
        started_at: float,
        *,
        image_error_state: dict[str, str | None],
    ) -> GenerationAttemptOutcome:
        ref_images = await self.media_service.normalize_ref_images(request.request_refs, client)
        if request.resolved_mode in {"edit", "selfie"} and not ref_images:
            raise ValueError(ref_image_unavailable())

        return await run_with_retries(
            self._build_attempt(
                client,
                event,
                payload=build_payload(
                    task.prompt,
                    runtime_config.model,
                    ref_images,
                    image_size=task.image_size,
                    reference_lines=self._get_reference_prompt_lines(request.resolved_mode),
                ),
                runtime_config=runtime_config,
                resolved_mode=request.resolved_mode,
                started_at=started_at,
                image_error_state=image_error_state,
            ),
            retry_count=runtime_config.retry_count,
            on_retry=lambda context: self._notify_retry(event, context),
            classify_exception=classify_retry_exception,
        )

    def _build_attempt(
        self,
        client: httpx.AsyncClient,
        event: AstrMessageEvent,
        *,
        payload: dict[str, object],
        runtime_config: GenerationRuntimeConfig,
        resolved_mode: GenerationMode,
        started_at: float,
        image_error_state: dict[str, str | None],
    ):
        url = runtime_config.base_url + "/v1/responses"
        headers = {
            "Authorization": f"Bearer {runtime_config.api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        async def attempt() -> GenerationAttemptOutcome:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    detail = f"HTTP {response.status_code} {summarize_error_body(body)}"
                    raise RetrySignalError(
                        detail,
                        retryable=should_retry_http_error(response.status_code, detail),
                    )
                return await self.stream_handler.stream_response(
                    response,
                    client,
                    event,
                    runtime_config,
                    resolved_mode,
                    started_at,
                    image_error_state,
                )

        return attempt

    async def _notify_retry(
        self,
        event: AstrMessageEvent,
        context: RetryContext,
    ) -> None:
        await self.sender.log_and_send(
            event,
            format_retry_notice(context),
            log_level="warning",
        )

    def _get_reference_prompt_lines(self, mode: GenerationMode) -> list[str]:
        if mode == "selfie":
            return get_reference_prompt_lines(
                mode,
                self.config_reader.get_text("reference_prompt_selfie", DEFAULT_REFERENCE_PROMPT_SELFIE),
            )
        if mode == "edit":
            return get_reference_prompt_lines(
                mode,
                self.config_reader.get_text("reference_prompt_edit", DEFAULT_REFERENCE_PROMPT_EDIT),
            )
        return get_reference_prompt_lines(mode)
