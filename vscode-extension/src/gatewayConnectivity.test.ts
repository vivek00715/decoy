import { checkGatewayConnectivity, GatewayRequestFn } from "./gatewayConnectivity";

const CREDS = { authToken: "fake-token", baseUrl: "https://gw.example.com", skipTlsVerify: false };

describe("checkGatewayConnectivity", () => {
  it("ok: 200 response classifies as ok, with the correct Bearer header and URL sent", async () => {
    const calls: Array<{ url: string; headers: Record<string, string>; skipTlsVerify: boolean }> = [];
    const requestFn: GatewayRequestFn = async (url, headers, _body, skipTlsVerify) => {
      calls.push({ url, headers, skipTlsVerify });
      return { status: 200, body: "{}" };
    };
    const result = await checkGatewayConnectivity(CREDS, requestFn);
    expect(result).toEqual({ ok: true });
    expect(calls[0].url).toBe("https://gw.example.com/v1/messages");
    expect(calls[0].headers.authorization).toBe("Bearer fake-token");
    expect(calls[0].skipTlsVerify).toBe(false);
  });

  it("401 classifies as unauthorized with a message naming the fix", async () => {
    const requestFn: GatewayRequestFn = async () => ({ status: 401, body: "unauthorized" });
    const result = await checkGatewayConnectivity(CREDS, requestFn);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("unauthorized");
      expect(result.message).toMatch(/token/i);
    }
  });

  it("403 also classifies as unauthorized", async () => {
    const requestFn: GatewayRequestFn = async () => ({ status: 403, body: "forbidden" });
    const result = await checkGatewayConnectivity(CREDS, requestFn);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.kind).toBe("unauthorized");
  });

  it("a thrown network error (unreachable host) classifies as unreachable, not a crash", async () => {
    const requestFn: GatewayRequestFn = async () => {
      throw new Error("getaddrinfo ENOTFOUND gw.example.com");
    };
    const result = await checkGatewayConnectivity(CREDS, requestFn);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("unreachable");
      expect(result.message).toContain("gw.example.com");
    }
  });

  it("a non-401/403 4xx/5xx classifies as http_error, body included for diagnosis", async () => {
    const requestFn: GatewayRequestFn = async () => ({ status: 500, body: "internal gateway error" });
    const result = await checkGatewayConnectivity(CREDS, requestFn);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("http_error");
      expect(result.message).toContain("500");
      expect(result.message).toContain("internal gateway error");
    }
  });

  it("trailing slash on baseUrl does not produce a double slash in the request URL", async () => {
    const calls: string[] = [];
    const requestFn: GatewayRequestFn = async (url) => {
      calls.push(url);
      return { status: 200, body: "{}" };
    };
    await checkGatewayConnectivity({ ...CREDS, baseUrl: "https://gw.example.com/" }, requestFn);
    expect(calls[0]).toBe("https://gw.example.com/v1/messages");
  });
});
