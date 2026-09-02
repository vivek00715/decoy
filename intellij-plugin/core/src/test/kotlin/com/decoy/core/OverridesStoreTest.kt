package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

class OverridesStoreTest {

    @Test
    fun `returns empty rules when no file exists`(@TempDir tmp: File) {
        assertEquals(emptyOverridesFile(), readOverrides(tmp))
    }

    @Test
    fun `reads a well-formed file written by the Python side's format`(@TempDir tmp: File) {
        File(tmp, ".decoy").mkdirs()
        File(tmp, OVERRIDES_RELATIVE_PATH).writeText(
            """
            {"always_mask": {"patterns": ["EMP-\\d{6}"], "field_names": ["employee_id"]},
             "never_mask": {"patterns": [], "field_names": ["id"]}}
            """.trimIndent(),
        )
        val result = readOverrides(tmp)
        assertEquals(listOf("employee_id"), result.always_mask.field_names)
        assertEquals(listOf("id"), result.never_mask.field_names)
    }

    @Test
    fun `fails safe to empty rules on malformed JSON`(@TempDir tmp: File) {
        File(tmp, ".decoy").mkdirs()
        File(tmp, OVERRIDES_RELATIVE_PATH).writeText("{ not valid json")
        assertEquals(emptyOverridesFile(), readOverrides(tmp))
    }

    @Test
    fun `round-trips through writeOverrides`(@TempDir tmp: File) {
        val data = OverridesFile(
            always_mask = OverrideRules(patterns = listOf("FOO-\\d+"), field_names = listOf("employee_id")),
            never_mask = OverrideRules(field_names = listOf("status")),
        )
        writeOverrides(tmp, data)
        assertEquals(data, readOverrides(tmp))
    }

    @Test
    fun `writes plain parseable JSON with no atomic-write artifacts left behind`(@TempDir tmp: File) {
        writeOverrides(tmp, emptyOverridesFile())
        val finalFile = File(tmp, OVERRIDES_RELATIVE_PATH)
        val tmpFile = File(finalFile.parentFile, finalFile.name + ".tmp")
        assert(finalFile.exists())
        assertFalse(tmpFile.exists())
    }

    @Test
    fun `clearOverrides resets an existing file to empty`(@TempDir tmp: File) {
        writeOverrides(
            tmp,
            OverridesFile(always_mask = OverrideRules(field_names = listOf("employee_id"))),
        )
        clearOverrides(tmp)
        assertEquals(emptyOverridesFile(), readOverrides(tmp))
    }

    @Test
    fun `clearOverrides does nothing if no file exists yet`(@TempDir tmp: File) {
        clearOverrides(tmp)
        assertFalse(File(tmp, OVERRIDES_RELATIVE_PATH).exists())
    }
}
