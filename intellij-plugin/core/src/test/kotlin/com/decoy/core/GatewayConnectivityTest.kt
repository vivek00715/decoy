package com.decoy.core

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

private val CREDS = GatewayCheckCredentials(authToken = "fake-token", baseUrl = "https://gw.example.com", skipTlsVerify = false)

class GatewayConnectivityTest {

    @Test
    fun `ok - 200 response classifies as ok, with the correct Bearer header and URL sent`() {
        val calls = mutableListOf<Triple<String, Map<String, String>, Boolean>>()
        val requestFn = GatewayRequestFn { url, headers, _, skipTlsVerify ->
            calls.add(Triple(url, headers, skipTlsVerify))
            GatewayHttpResponse(200, "{}")
        }
        val result = checkGatewayConnectivity(CREDS, requestFn)
        assertEquals(GatewayCheckResult.Ok, result)
        assertEquals("https://gw.example.com/v1/messages", calls[0].first)
        assertEquals("Bearer fake-token", calls[0].second["authorization"])
        assertFalse(calls[0].third)
    }

    @Test
    fun `401 classifies as unauthorized with a message naming the fix`() {
        val requestFn = GatewayRequestFn { _, _, _, _ -> GatewayHttpResponse(401, "unauthorized") }
        val result = checkGatewayConnectivity(CREDS, requestFn)
        assertTrue(result is GatewayCheckResult.Failed)
        result as GatewayCheckResult.Failed
        assertEquals(GatewayCheckResult.Failed.Kind.UNAUTHORIZED, result.kind)
        assertTrue(result.message.contains("token", ignoreCase = true))
    }

    @Test
    fun `403 also classifies as unauthorized`() {
        val requestFn = GatewayRequestFn { _, _, _, _ -> GatewayHttpResponse(403, "forbidden") }
        val result = checkGatewayConnectivity(CREDS, requestFn) as GatewayCheckResult.Failed
        assertEquals(GatewayCheckResult.Failed.Kind.UNAUTHORIZED, result.kind)
    }

    @Test
    fun `a thrown exception (unreachable host) classifies as unreachable, not a crash`() {
        val requestFn = GatewayRequestFn { _, _, _, _ -> throw java.net.UnknownHostException("gw.example.com") }
        val result = checkGatewayConnectivity(CREDS, requestFn)
        assertTrue(result is GatewayCheckResult.Failed)
        result as GatewayCheckResult.Failed
        assertEquals(GatewayCheckResult.Failed.Kind.UNREACHABLE, result.kind)
        assertTrue(result.message.contains("gw.example.com"))
    }

    @Test
    fun `a non-401,403 4xx,5xx classifies as http_error, body included for diagnosis`() {
        val requestFn = GatewayRequestFn { _, _, _, _ -> GatewayHttpResponse(500, "internal gateway error") }
        val result = checkGatewayConnectivity(CREDS, requestFn) as GatewayCheckResult.Failed
        assertEquals(GatewayCheckResult.Failed.Kind.HTTP_ERROR, result.kind)
        assertTrue(result.message.contains("500"))
        assertTrue(result.message.contains("internal gateway error"))
    }

    @Test
    fun `trailing slash on baseUrl does not produce a double slash in the request URL`() {
        val calls = mutableListOf<String>()
        val requestFn = GatewayRequestFn { url, _, _, _ ->
            calls.add(url)
            GatewayHttpResponse(200, "{}")
        }
        checkGatewayConnectivity(CREDS.copy(baseUrl = "https://gw.example.com/"), requestFn)
        assertEquals("https://gw.example.com/v1/messages", calls[0])
    }
}
