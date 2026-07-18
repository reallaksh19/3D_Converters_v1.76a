/**
 * Functionality: translates Preview resolver/fill-down sources into explicit
 * provenance and visible badge metadata.
 * Parameters: override metadata, field/key, and resolved source.
 * Output: pure provenance strings/display records; no UI or config mutation.
 */

export function previewOverrideProvenance(overrides, bucket, key, resolvedSource) {
  if (resolvedSource !== 'override') return resolvedSource;
  const fillState = overrides?.__previewFillDown?.[bucket]?.[key]?.fillState;
  if (fillState === 'manual') return 'manual-override';
  if (fillState === 'auto') return 'auto-fill';
  return 'saved-override';
}

export function previewProvenanceBadge(field, source) {
  if (source === 'manual-override' || source === 'override') return { label: '✓ override', className: 'exact', title: 'Manually overridden value.' };
  if (source === 'auto-fill') return { label: 'auto-fill', className: 'exact', title: 'Automatically propagated by Preview fill-down; not a manual cell override.' };
  if (source === 'saved-override') return { label: 'saved', className: 'exact', title: 'Saved override value from an older config; manual provenance is unavailable.' };
  if (source === 'piping-class-master' && (field === 'wallThickness' || field === 'corrosion')) return { label: 'derived', className: 'exact', title: 'Derived from the effective Piping Class master row; not manually overridden.' };
  if (source === 'dtxr-sch-applied') return { label: '✓ DTXR Sch', className: 'exact', title: 'Wall thickness applied from DTXR schedule. Click cell to override manually.' };
  if (source === 'default' || source === 'config-default' || source === 'default-zero') return { label: 'default', className: 'bad', title: 'Config default value.' };
  return null;
}
