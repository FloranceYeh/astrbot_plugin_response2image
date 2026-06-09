from dataclasses import dataclass

try:
    from .config import PluginConfigReader
    from .generation import GenerationInputs, RefInput, merge_refs, normalize_image_size, resolve_generation_inputs
    from .messages import preset_not_found
    from .storage import PromptPresetStore
except ImportError:
    from core.config import PluginConfigReader
    from core.generation import GenerationInputs, RefInput, merge_refs, normalize_image_size, resolve_generation_inputs
    from core.messages import preset_not_found
    from core.storage import PromptPresetStore


@dataclass(slots=True)
class PresetResolver:
    config_reader: PluginConfigReader
    preset_store: PromptPresetStore

    def resolve(
        self,
        raw_prompt: str,
        *,
        ref: RefInput = None,
        size: str = "",
        preset: str = "",
    ) -> GenerationInputs:
        default_image_size = normalize_image_size(self.config_reader.get_str("image_size", ""))
        inputs = resolve_generation_inputs(
            raw_prompt,
            ref,
            size,
            preset=preset,
            default_image_size=None,
        )
        if not inputs.preset_title:
            inputs.image_size = inputs.image_size or default_image_size
            return inputs

        preset_item = self.preset_store.get_preset(inputs.preset_title)
        if preset_item is None:
            raise ValueError(preset_not_found(inputs.preset_title))

        inputs.prompt = self._merge_prompt(preset_item.content, inputs.prompt)
        inputs.ref_urls = merge_refs(inputs.ref_urls, preset_item.ref_urls)
        inputs.image_size = inputs.image_size or preset_item.image_size or default_image_size
        return inputs

    def _merge_prompt(self, preset_prompt: str, input_prompt: str) -> str:
        normalized_preset = preset_prompt.strip()
        normalized_input = input_prompt.strip()
        if not normalized_input:
            return normalized_preset
        if not normalized_preset:
            return normalized_input
        return f"{normalized_preset}\n{normalized_input}"
