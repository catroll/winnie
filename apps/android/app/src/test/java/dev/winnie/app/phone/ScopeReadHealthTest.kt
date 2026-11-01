package dev.winnie.app.phone

import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The three answers a content provider can give, and why they must stay
 * distinguishable.
 *
 * A phone that cannot read its call log must not look like a phone with an
 * empty call log: the first is a permission/privacy problem, the second is a
 * fact about the owner's data. `recordRead` is what carries that difference to
 * the server.
 */
class ScopeReadHealthTest {

    @Test
    fun grantedAndReadableKeepsTheRowCount() {
        val json = recordRead(granted = true) { 42 }

        assertEquals("granted", json["permission"]?.jsonPrimitive?.content)
        assertEquals(42, json["rows"]?.jsonPrimitive?.int)
        assertTrue(json["error"] == null)
    }

    @Test
    fun aDeniedGrantIsReportedEvenWhenTheProviderAnswers() {
        val json = recordRead(granted = false) { 0 }

        assertEquals("denied", json["permission"]?.jsonPrimitive?.content)
        assertEquals(0, json["rows"]?.jsonPrimitive?.int)
    }

    @Test
    fun aNullCursorIsAnErrorNotAnEmptyDataset() {
        val json = recordRead(granted = true) { null }

        assertEquals("null_cursor", json["error"]?.jsonPrimitive?.content)
        // No row count at all: "unknown" must not be stored as "zero".
        assertTrue(json["rows"] == null)
    }

    @Test
    fun aSecurityExceptionKeepsItsMessage() {
        val json = recordRead(granted = true) {
            throw SecurityException("Permission Denial: reading com.android.providers.telephony")
        }

        val error = json["error"]?.jsonPrimitive?.content.orEmpty()
        assertTrue(error.startsWith("security: "))
        assertTrue(error.contains("Permission Denial"))
        assertTrue(json["rows"] == null)
    }

    @Test
    fun anyOtherProviderFailureKeepsItsClass() {
        val json = recordRead(granted = true) {
            throw IllegalArgumentException("Unknown column")
        }

        val error = json["error"]?.jsonPrimitive?.content.orEmpty()
        assertTrue(error.startsWith("IllegalArgumentException: "))
        assertTrue(error.contains("Unknown column"))
    }
}
