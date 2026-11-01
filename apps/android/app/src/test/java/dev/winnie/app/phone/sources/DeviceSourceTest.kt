package dev.winnie.app.phone.sources

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import dev.winnie.app.BuildConfig
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The device profile is assembled from many guarded probes, so the test asserts
 * the shape of what is always present and that a probe without a permission
 * leaves its key out instead of failing the snapshot.
 */
@RunWith(RobolectricTestRunner::class)
class DeviceSourceTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun snapshotCarriesIdentityAndNetworkFields() {
        val snapshot = DeviceSource(context).snapshot(listOf("device"))

        assertEquals(BuildConfig.VERSION_NAME, snapshot.appVersion)
        assertTrue(snapshot.info.containsKey("sdk_int"))
        assertTrue(snapshot.info.containsKey("fingerprint"))
        assertTrue(snapshot.info.containsKey("security_patch"))
        assertTrue(snapshot.info.containsKey("network"))
        // Interface enumeration needs no permission, so both families are always
        // present, even when empty.
        assertTrue(snapshot.info.containsKey("ipv4"))
        assertTrue(snapshot.info.containsKey("ipv6"))
    }

    @Test
    fun theProfileCarriesWhatTheProviderScopesAnswered() {
        val snapshot = DeviceSource(context).snapshot(listOf("device"))

        // The server cannot tell "no contacts" from "not allowed to read
        // contacts" without this; it must travel with every device snapshot.
        val reads = snapshot.info["scope_read"]?.jsonObject
        assertTrue(reads != null)
        for (kind in listOf("message", "call", "contact", "calendar", "calendars")) {
            assertTrue("scope_read.$kind is missing", reads!!.containsKey(kind))
        }
        // Robolectric registers no provider, so every probe answers with a null
        // cursor instead of inventing a row count.
        assertEquals(
            "null_cursor",
            reads!!["contact"]?.jsonObject?.get("error")?.jsonPrimitive?.content,
        )
    }

    @Test
    fun aProbeWithoutItsPermissionLeavesTheKeyOut() {
        val snapshot = DeviceSource(context).snapshot(emptyList())

        // Robolectric has no SIM and no location grant: the phone number and the
        // WiFi SSID are unknown, and an unknown value must not be stored as one.
        assertFalse(snapshot.info.containsKey("phone_number"))
        assertFalse(snapshot.info.containsKey("wifi_ssid"))
    }

    @Test
    fun registrationCarriesTheSameProfile() {
        val payload = DeviceSource(context).registration("device-key-1234", listOf("device"))

        assertEquals("device-key-1234", payload.deviceKey)
        assertEquals("android", payload.platform)
        assertEquals(BuildConfig.VERSION_NAME, payload.appVersion)
        assertTrue(payload.info.containsKey("sdk_int"))
    }
}
