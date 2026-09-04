package com.decoy.core

import kotlinx.serialization.SerializationException
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * The JCEF webview's JS posts one JSON object per user action, shaped
 * identically to the VS Code extension's `WebviewMessage` union in
 * panelController.ts (`{command: "refresh"}`,
 * `{command: "clearSession", sessionId}`,
 * `{command: "clearSessionDataOnly"}`, `{command: "clearAll"}`,
 * `{command: "saveOverrides", overrides}`) -- see WebviewContent.kt's
 * `post()` JS helper. Kotlin has no first-class discriminated-union
 * decoding as convenient as TypeScript's here, so this is one flat data
 * class with the fields only some commands use left null; unknown/
 * unexpected shapes fail to `null` rather than throwing, matching this
 * module's established fail-safe style (see e.g. readOverrides).
 */
@Serializable
data class WebviewMessage(
    val command: String,
    val sessionId: String? = null,
    val overrides: OverridesFile? = null,
)

private val json = Json { ignoreUnknownKeys = true }

/** Parses one JS-posted message. Returns null on anything unparseable
 * (malformed JSON, missing `command`) rather than throwing -- a single
 * bad message from the webview should never crash the plugin. */
fun parseWebviewMessage(raw: String): WebviewMessage? = try {
    json.decodeFromString<WebviewMessage>(raw)
} catch (_: SerializationException) {
    null
} catch (_: IllegalArgumentException) {
    null
}
