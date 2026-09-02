package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

class OverrideEditsTest {

    // -- addOverrideEntry --------------------------------------------------

    @Test
    fun `add appends a new field name to always_mask`() {
        val result = addOverrideEntry(emptyOverridesFile(), OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "employee_id")
        assertEquals(listOf("employee_id"), result.always_mask.field_names)
    }

    @Test
    fun `add appends a new pattern to always_mask`() {
        val result = addOverrideEntry(emptyOverridesFile(), OverrideKind.ALWAYS_MASK, OverrideListKind.PATTERNS, "EMP-\\d{6}")
        assertEquals(listOf("EMP-\\d{6}"), result.always_mask.patterns)
    }

    @Test
    fun `add appends a new field name to never_mask`() {
        val result = addOverrideEntry(emptyOverridesFile(), OverrideKind.NEVER_MASK, OverrideListKind.FIELD_NAMES, "status")
        assertEquals(listOf("status"), result.never_mask.field_names)
    }

    @Test
    fun `add appends a new pattern to never_mask`() {
        val result = addOverrideEntry(emptyOverridesFile(), OverrideKind.NEVER_MASK, OverrideListKind.PATTERNS, "^ok$")
        assertEquals(listOf("^ok$"), result.never_mask.patterns)
    }

    @Test
    fun `add is a no-op for a duplicate value`() {
        val withOne = addOverrideEntry(emptyOverridesFile(), OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "employee_id")
        val withDuplicate = addOverrideEntry(withOne, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "employee_id")
        assertEquals(listOf("employee_id"), withDuplicate.always_mask.field_names)
    }

    @Test
    fun `add is a no-op for a blank value`() {
        val result = addOverrideEntry(emptyOverridesFile(), OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "   ")
        assertEquals(emptyOverridesFile(), result)
    }

    // -- removeOverrideEntry -------------------------------------------------

    @Test
    fun `remove deletes an existing entry`() {
        val withOne = addOverrideEntry(emptyOverridesFile(), OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "employee_id")
        val result = removeOverrideEntry(withOne, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "employee_id")
        assertEquals(emptyList<String>(), result.always_mask.field_names)
    }

    @Test
    fun `remove is a no-op for a non-existent entry`() {
        val original = emptyOverridesFile()
        val result = removeOverrideEntry(original, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "does_not_exist")
        assertEquals(original, result)
    }

    @Test
    fun `remove only touches the specified kind and list, leaving others untouched`() {
        var overrides = addOverrideEntry(emptyOverridesFile(), OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "a")
        overrides = addOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.PATTERNS, "p1")
        overrides = addOverrideEntry(overrides, OverrideKind.NEVER_MASK, OverrideListKind.FIELD_NAMES, "b")

        val result = removeOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "a")

        assertEquals(emptyList<String>(), result.always_mask.field_names)
        assertEquals(listOf("p1"), result.always_mask.patterns)
        assertEquals(listOf("b"), result.never_mask.field_names)
    }

    // -- editOverrideEntry: normal case --------------------------------------

    @Test
    fun `edit replaces in place preserving position`() {
        var overrides = emptyOverridesFile()
        overrides = addOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "first")
        overrides = addOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "second")
        overrides = addOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "third")

        val result = editOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "second", "middle")

        assertEquals(listOf("first", "middle", "third"), result.always_mask.field_names)
    }

    @Test
    fun `edit is a no-op when newValue equals oldValue`() {
        val overrides = addOverrideEntry(emptyOverridesFile(), OverrideKind.NEVER_MASK, OverrideListKind.PATTERNS, "^ok$")
        val result = editOverrideEntry(overrides, OverrideKind.NEVER_MASK, OverrideListKind.PATTERNS, "^ok$", "^ok$")
        assertEquals(overrides, result)
    }

    // -- editOverrideEntry: fallback edge cases ------------------------------

    @Test
    fun `edit falls back to append when oldValue is not present`() {
        val overrides = addOverrideEntry(emptyOverridesFile(), OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "existing")
        val result = editOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "not_there", "new_one")
        assertEquals(listOf("existing", "new_one"), result.always_mask.field_names)
    }

    @Test
    fun `edit falls back to remove when newValue is blank`() {
        val overrides = addOverrideEntry(emptyOverridesFile(), OverrideKind.NEVER_MASK, OverrideListKind.FIELD_NAMES, "status")
        val result = editOverrideEntry(overrides, OverrideKind.NEVER_MASK, OverrideListKind.FIELD_NAMES, "status", "   ")
        assertEquals(emptyList<String>(), result.never_mask.field_names)
    }

    @Test
    fun `edit merges when newValue collides with a different existing entry, dropping oldValue without duplicating newValue`() {
        var overrides = emptyOverridesFile()
        overrides = addOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "alpha")
        overrides = addOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "beta")
        overrides = addOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "gamma")

        // editing "alpha" -> "gamma" (gamma already exists elsewhere)
        val result = editOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "alpha", "gamma")

        assertEquals(listOf("beta", "gamma"), result.always_mask.field_names) // alpha dropped, no duplicate gamma
    }

    // -- full read-modify-write round trip -----------------------------------

    @Test
    fun `remove and edit round-trip correctly through readOverrides writeOverrides`(@TempDir tmp: File) {
        var overrides = emptyOverridesFile()
        overrides = addOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "employee_id")
        overrides = addOverrideEntry(overrides, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "ssn")
        overrides = addOverrideEntry(overrides, OverrideKind.NEVER_MASK, OverrideListKind.FIELD_NAMES, "status")
        writeOverrides(tmp, overrides)

        // simulate a UI session: read current state, remove one entry,
        // edit another, write back
        val loaded = readOverrides(tmp)
        var updated = removeOverrideEntry(loaded, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "ssn")
        updated = editOverrideEntry(updated, OverrideKind.ALWAYS_MASK, OverrideListKind.FIELD_NAMES, "employee_id", "emp_id")
        writeOverrides(tmp, updated)

        val final = readOverrides(tmp)
        assertEquals(listOf("emp_id"), final.always_mask.field_names)
        assertEquals(listOf("status"), final.never_mask.field_names) // untouched kind
        assertEquals(emptyList<String>(), final.always_mask.patterns) // untouched list within the same kind
    }
}
