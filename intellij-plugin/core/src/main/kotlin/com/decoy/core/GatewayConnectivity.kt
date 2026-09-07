package com.decoy.core

import java.io.IOException
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import java.security.cert.X509Certificate

/**
 * Mirrors vscode-extension/src/gatewayConnectivity.ts exactly -- answers
 * "what does the user see if the gateway is unreachable or the token is
 * rejected" by actually probing the gateway with a minimal real request,
 * classified into a small result type the plugin layer can render.
 *
 * This matters specifically for gateway mode: ChatProxyProcessManager's
 * process-level state only tracks whether the LOCAL proxy process is
 * alive -- an unreachable/401 upstream gateway is invisible to it, since
 * the local proxy still starts up fine even if every request it later
 * forwards gets rejected.
 *
 * `requestFn` is injected (same DI pattern this project uses throughout --
 * ChatProxyProcessManager's ProcessBuilder, McpApproval's ProcessBuilder)
 * so this is testable against a fake, never a real network call in
 * tests. `httpClientGatewayRequest` below is the one real implementation.
 */

sealed class GatewayCheckResult {
    object Ok : GatewayCheckResult()
    data class Failed(val kind: Kind, val message: String) : GatewayCheckResult() {
        enum class Kind { UNAUTHORIZED, UNREACHABLE, HTTP_ERROR }
    }
}

data class GatewayCheckCredentials(val authToken: String, val baseUrl: String, val skipTlsVerify: Boolean)

data class GatewayHttpResponse(val status: Int, val body: String)

fun interface GatewayRequestFn {
    fun send(url: String, headers: Map<String, String>, body: String, skipTlsVerify: Boolean): GatewayHttpResponse
}

/** Sends the same minimal shape chat_proxy.py's messages() handler
 * forwards upstream, so a pass here means a real end-to-end auth
 * round-trip succeeded -- not just "the URL resolves". */
fun checkGatewayConnectivity(creds: GatewayCheckCredentials, requestFn: GatewayRequestFn): GatewayCheckResult {
    val url = creds.baseUrl.trimEnd('/') + "/v1/messages"
    val body = """{"model":"claude-haiku-4-5","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}"""

    val response = try {
        requestFn.send(
            url,
            mapOf(
                "authorization" to "Bearer ${creds.authToken}",
                "content-type" to "application/json",
                "anthropic-version" to "2023-06-01",
            ),
            body,
            creds.skipTlsVerify,
        )
    } catch (e: Exception) {
        return GatewayCheckResult.Failed(
            GatewayCheckResult.Failed.Kind.UNREACHABLE,
            "Decoy: could not reach ${creds.baseUrl} (${e.message}). Check the gateway URL, your " +
                "network connection, and whether TLS verification needs to be skipped for this " +
                "network (Set Credentials -> Org Gateway).",
        )
    }

    if (response.status == 401 || response.status == 403) {
        return GatewayCheckResult.Failed(
            GatewayCheckResult.Failed.Kind.UNAUTHORIZED,
            "Decoy: gateway rejected the auth token (HTTP ${response.status}). Re-run \"Set Credentials\" with a valid token.",
        )
    }
    if (response.status >= 400) {
        return GatewayCheckResult.Failed(
            GatewayCheckResult.Failed.Kind.HTTP_ERROR,
            "Decoy: gateway returned HTTP ${response.status}: ${response.body.take(200)}",
        )
    }
    return GatewayCheckResult.Ok
}

/** Trusts everything -- ONLY constructed when skipTlsVerify is explicitly
 * true, mirroring DECOY_UPSTREAM_SKIP_TLS_VERIFY's opt-in-only contract
 * on the chat_proxy.py side. Never used by default. */
private object TrustAllManager : X509TrustManager {
    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
    override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
}

/** Real implementation using java.net.http.HttpClient -- no new
 * dependency needed. Not used by any test (tests inject a fake
 * GatewayRequestFn instead); wired in by DecoyProjectService.kt only. */
fun httpClientGatewayRequest(url: String, headers: Map<String, String>, body: String, skipTlsVerify: Boolean): GatewayHttpResponse {
    val clientBuilder = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10))
    if (skipTlsVerify) {
        val sslContext = SSLContext.getInstance("TLS")
        sslContext.init(null, arrayOf<TrustManager>(TrustAllManager), java.security.SecureRandom())
        clientBuilder.sslContext(sslContext)
    }
    val client = clientBuilder.build()

    val requestBuilder = HttpRequest.newBuilder(URI.create(url))
        .timeout(Duration.ofSeconds(10))
        .POST(HttpRequest.BodyPublishers.ofString(body))
    headers.forEach { (key, value) -> requestBuilder.header(key, value) }

    val response = try {
        client.send(requestBuilder.build(), HttpResponse.BodyHandlers.ofString())
    } catch (e: IOException) {
        throw e
    }
    return GatewayHttpResponse(response.statusCode(), response.body())
}
