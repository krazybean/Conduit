import type { Driver } from "./types.js";
import manifest from "./providers.json" with { type: "json" };

export type ProviderPreset = keyof typeof manifest.providers;

export interface ProviderPresetDefinition {
  driver: Driver;
  endpoint: string;
  /** Preset metadata is descriptive; it is not a Conduit compatibility claim. */
  compatibility: "unverified";
}

export const providerManifest = manifest;
export const providerPresets: Readonly<Record<ProviderPreset, ProviderPresetDefinition>> = Object.freeze(manifest.providers as Record<ProviderPreset, ProviderPresetDefinition>);

export function preset(name: ProviderPreset): ProviderPresetDefinition {
  return providerPresets[name];
}
