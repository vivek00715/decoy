package com.decoy.core

/** Which override section a value belongs to. */
enum class OverrideKind { ALWAYS_MASK, NEVER_MASK }

/** Which list within that section -- field names or regex patterns. */
enum class OverrideListKind { FIELD_NAMES, PATTERNS }

private fun getList(overrides: OverridesFile, kind: OverrideKind, list: OverrideListKind): List<String> {
    val rules = if (kind == OverrideKind.ALWAYS_MASK) overrides.always_mask else overrides.never_mask
    return if (list == OverrideListKind.FIELD_NAMES) rules.field_names else rules.patterns
}

private fun withList(overrides: OverridesFile, kind: OverrideKind, list: OverrideListKind, newValues: List<String>): OverridesFile {
    return if (kind == OverrideKind.ALWAYS_MASK) {
        val rules = overrides.always_mask
        overrides.copy(
            always_mask = if (list == OverrideListKind.FIELD_NAMES) rules.copy(field_names = newValues) else rules.copy(patterns = newValues),
        )
    } else {
        val rules = overrides.never_mask
        overrides.copy(
            never_mask = if (list == OverrideListKind.FIELD_NAMES) rules.copy(field_names = newValues) else rules.copy(patterns = newValues),
        )
    }
}

/**
 * Add `value` to the given list if not already present (case-sensitive
 * exact match). No-op (returns an OverridesFile equal to `overrides`) if
 * `value` is blank or already present -- keeps the list free of
 * duplicates and empty entries, since both would be silently useless
 * overrides a user could easily end up with by double-clicking "Add".
 */
fun addOverrideEntry(overrides: OverridesFile, kind: OverrideKind, list: OverrideListKind, value: String): OverridesFile {
    if (value.isBlank()) return overrides
    val current = getList(overrides, kind, list)
    if (value in current) return overrides
    return withList(overrides, kind, list, current + value)
}

/** Remove `value` from the given list if present. No-op if not present. */
fun removeOverrideEntry(overrides: OverridesFile, kind: OverrideKind, list: OverrideListKind, value: String): OverridesFile {
    val current = getList(overrides, kind, list)
    if (value !in current) return overrides
    return withList(overrides, kind, list, current.filter { it != value })
}

/**
 * Replace `oldValue` with `newValue` in the given list, preserving
 * position. Three edge cases, each falling back to a simpler operation
 * rather than being treated as an error, since a UI-driven edit dialog
 * can easily produce any of them from ordinary user action:
 *
 * - `oldValue` isn't present: equivalent to [addOverrideEntry] (append
 *   `newValue`) -- e.g. the list changed underneath the UI between
 *   opening the edit dialog and confirming it.
 * - `newValue` is blank: equivalent to [removeOverrideEntry] on
 *   `oldValue` -- clearing the edit field is treated as "delete this
 *   entry," not as an invalid empty-string override.
 * - `newValue` already exists elsewhere in the list (a genuine
 *   collision, not the trivial no-op case newValue == oldValue): the
 *   two entries MERGE -- `oldValue` is removed and `newValue`'s existing
 *   position is left untouched, rather than producing a duplicate.
 *   `oldValue`'s original position is NOT preserved in this specific
 *   case, since there is no sensible single position for a value that
 *   already exists elsewhere in the list.
 *
 * In the ordinary case (both present exactly once, no collision),
 * `oldValue` is replaced by `newValue` at its exact original index.
 */
fun editOverrideEntry(overrides: OverridesFile, kind: OverrideKind, list: OverrideListKind, oldValue: String, newValue: String): OverridesFile {
    if (newValue.isBlank()) return removeOverrideEntry(overrides, kind, list, oldValue)

    val current = getList(overrides, kind, list)
    val oldIndex = current.indexOf(oldValue)
    if (oldIndex < 0) return addOverrideEntry(overrides, kind, list, newValue)
    if (newValue == oldValue) return overrides

    return if (newValue in current) {
        // Collision merge: drop oldValue, newValue already occupies a
        // position elsewhere in the list.
        withList(overrides, kind, list, current.filter { it != oldValue })
    } else {
        withList(overrides, kind, list, current.toMutableList().also { it[oldIndex] = newValue })
    }
}
