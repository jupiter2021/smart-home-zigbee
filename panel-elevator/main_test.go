package main

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const testToken = "test-token-012345678901234567890123"

func testConfig() Config {
	return Config{
		ListenAddr:      "127.0.0.1:9998",
		TimeoutSeconds:  1,
		CooldownSeconds: 30,
		SIP: SIPConfig{
			SourceIP: "127.0.0.1",
			SourceID: "1000001",
			TargetIP: "127.0.0.1",
			TargetID: "1000002",
			Port:     5060,
			Floor:    1,
			Family:   1,
		},
	}
}

func requestFor(id string) *http.Request {
	req := httptest.NewRequest(http.MethodPost, "/api/elevator/down", strings.NewReader(fmt.Sprintf(`{"request_id":%q}`, id)))
	req.Header.Set("Authorization", "Bearer "+testToken)
	return req
}

func decodeResponse(t *testing.T, recorder *httptest.ResponseRecorder) response {
	t.Helper()
	var got response
	if err := json.Unmarshal(recorder.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return got
}

func TestHTTPContractAndStrictValidation(t *testing.T) {
	calls := 0
	service, err := newService(testConfig(), testToken, func(SIPConfig, time.Duration) sendOutcome {
		calls++
		return sendOutcome{Kind: outcomeAccepted, SIPStatus: intPtr(200), ResultCode: intPtr(200)}
	}, time.Now)
	if err != nil {
		t.Fatal(err)
	}

	badAuth := requestFor("0123456789abcdef0123456789abcdef")
	badAuth.Header.Set("Authorization", "Bearer wrong-token")
	recorder := httptest.NewRecorder()
	service.Handler().ServeHTTP(recorder, badAuth)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("auth status=%d", recorder.Code)
	}
	if got := decodeResponse(t, recorder); got.Status != "error" || got.RequestID != "" {
		t.Fatalf("auth response=%+v", got)
	}

	for _, tc := range []struct {
		name, method, path, body, wantStatus string
		code                                 int
	}{
		{"method", http.MethodGet, "/api/elevator/down", `{"request_id":"0123456789abcdef0123456789abcdef"}`, "rejected", http.StatusMethodNotAllowed},
		{"path", http.MethodPost, "/api/elevator/up", `{"request_id":"0123456789abcdef0123456789abcdef"}`, "rejected", http.StatusNotFound},
		{"unknown field", http.MethodPost, "/api/elevator/down", `{"request_id":"0123456789abcdef0123456789abcdef","door":true}`, "error", http.StatusBadRequest},
		{"multiple JSON", http.MethodPost, "/api/elevator/down", `{"request_id":"0123456789abcdef0123456789abcdef"}{}`, "error", http.StatusBadRequest},
		{"duplicate field", http.MethodPost, "/api/elevator/down", `{"request_id":"0123456789abcdef0123456789abcdef","request_id":"abcdef0123456789abcdef0123456789"}`, "error", http.StatusBadRequest},
		{"uppercase ID", http.MethodPost, "/api/elevator/down", `{"request_id":"0123456789ABCDEF0123456789abcdef"}`, "error", http.StatusBadRequest},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
			req.Header.Set("Authorization", "Bearer "+testToken)
			recorder := httptest.NewRecorder()
			service.Handler().ServeHTTP(recorder, req)
			if recorder.Code != tc.code || decodeResponse(t, recorder).Status != tc.wantStatus {
				t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
			}
		})
	}

	recorder = httptest.NewRecorder()
	service.Handler().ServeHTTP(recorder, requestFor("0123456789abcdef0123456789abcdef"))
	if recorder.Code != http.StatusOK || decodeResponse(t, recorder).Status != "accepted" || calls != 1 {
		t.Fatalf("accepted status=%d calls=%d", recorder.Code, calls)
	}
	recorder = httptest.NewRecorder()
	service.Handler().ServeHTTP(recorder, requestFor("0123456789abcdef0123456789abcdef"))
	if recorder.Code != http.StatusConflict || decodeResponse(t, recorder).Status != "duplicate" {
		t.Fatalf("duplicate status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	recorder = httptest.NewRecorder()
	service.Handler().ServeHTTP(recorder, requestFor("abcdef0123456789abcdef0123456789"))
	if recorder.Code != http.StatusTooManyRequests || decodeResponse(t, recorder).Status != "cooldown" {
		t.Fatalf("cooldown status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestInFlightBlocksConcurrentRequest(t *testing.T) {
	cfg := testConfig()
	cfg.CooldownSeconds = 1
	started := make(chan struct{})
	release := make(chan struct{})
	var mu sync.Mutex
	var clockMu sync.Mutex
	clock := time.Unix(1000, 0)
	calls := 0
	service, err := newService(cfg, testToken, func(SIPConfig, time.Duration) sendOutcome {
		mu.Lock()
		calls++
		mu.Unlock()
		clockMu.Lock()
		clock = clock.Add(2 * time.Second)
		clockMu.Unlock()
		close(started)
		<-release
		return sendOutcome{Kind: outcomeAccepted, SIPStatus: intPtr(200), ResultCode: intPtr(200)}
	}, func() time.Time {
		clockMu.Lock()
		defer clockMu.Unlock()
		return clock
	})
	if err != nil {
		t.Fatal(err)
	}
	firstDone := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		recorder := httptest.NewRecorder()
		service.Handler().ServeHTTP(recorder, requestFor("0123456789abcdef0123456789abcdef"))
		firstDone <- recorder
	}()
	<-started
	second := httptest.NewRecorder()
	service.Handler().ServeHTTP(second, requestFor("abcdef0123456789abcdef0123456789"))
	if second.Code != http.StatusTooManyRequests {
		t.Fatalf("in-flight status=%d", second.Code)
	}
	secondBody := decodeResponse(t, second)
	if secondBody.Status != "cooldown" || secondBody.RetryAfterSeconds == nil || *secondBody.RetryAfterSeconds < 1 {
		t.Fatalf("in-flight response=%+v", secondBody)
	}
	close(release)
	if first := <-firstDone; first.Code != http.StatusOK {
		t.Fatalf("first status=%d", first.Code)
	}
	mu.Lock()
	defer mu.Unlock()
	if calls != 1 {
		t.Fatalf("calls=%d", calls)
	}
}

func TestTokenAndConfigValidation(t *testing.T) {
	for _, token := range []string{"short", strings.Repeat("a", 32) + " ", strings.Repeat("a", 32) + "\n"} {
		if _, err := NewService(testConfig(), token); err == nil {
			t.Errorf("token %q accepted", token)
		}
	}
	for _, addr := range []string{"0.0.0.0:9998", ":9998", "127.0.0.1:0", "127.0.0.1:65536", "localhost:9998"} {
		cfg := testConfig()
		cfg.ListenAddr = addr
		if _, err := NewService(cfg, testToken); err == nil {
			t.Errorf("listen address %q accepted", addr)
		}
	}
	tmp := t.TempDir()
	path := filepath.Join(tmp, "config.json")
	good := `{"sip":{"source_ip":"192.0.2.10","source_id":"1","target_ip":"192.0.2.20","target_id":"2","floor":1,"family":1}}`
	if err := os.WriteFile(path, []byte(good), 0600); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadConfig(path)
	if err != nil || loaded.ListenAddr != defaultListenAddr || loaded.SIP.Port != defaultSIPPort {
		t.Fatalf("loaded=%+v err=%v", loaded, err)
	}
	bad := `{"sip":{"source_ip":"192.0.2.10","source_id":"1","target_ip":"192.0.2.20","target_id":"2","floor":1,"family":1,"door":true}}`
	if err := os.WriteFile(path, []byte(bad), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadConfig(path); err == nil {
		t.Fatal("unknown config field accepted")
	}
	for _, tc := range []struct {
		name string
		data string
	}{
		{
			name: "duplicate top-level key",
			data: `{"listen_addr":"127.0.0.1:9998","listen_addr":"127.0.0.1:9999","sip":{"source_ip":"192.0.2.10","source_id":"1","target_ip":"192.0.2.20","target_id":"2","floor":1,"family":1}}`,
		},
		{
			name: "duplicate nested key",
			data: `{"sip":{"source_ip":"192.0.2.10","source_ip":"192.0.2.11","source_id":"1","target_ip":"192.0.2.20","target_id":"2","floor":1,"family":1}}`,
		},
		{
			name: "duplicate key in array object",
			data: `{"sip":{"source_ip":"192.0.2.10","source_id":"1","target_ip":"192.0.2.20","target_id":"2","floor":1,"family":1},"values":[{"name":1,"name":2}]}`,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := rejectDuplicateJSONKeys([]byte(tc.data)); err == nil {
				t.Fatal("duplicate JSON key accepted")
			}
			errPath := filepath.Join(tmp, tc.name+".json")
			if err := os.WriteFile(errPath, []byte(tc.data), 0600); err != nil {
				t.Fatal(err)
			}
			if _, err := LoadConfig(errPath); err == nil {
				t.Fatal("duplicate configuration key accepted")
			}
		})
	}
	if err := rejectDuplicateJSONKeys([]byte(`{"sip":{"source_ip":"192.0.2.10","source_id":"1","target_ip":"192.0.2.20","target_id":"2","floor":1,"family":1},"values":[{"name":1},{"name":2}]}`)); err != nil {
		t.Fatalf("normal repeated keys in separate objects rejected: %v", err)
	}
}

func TestSIPResponsesAndUTF8Length(t *testing.T) {
	for _, tc := range []struct {
		name       string
		status     int
		resultCode *int
		prefix     string
		want       outcomeKind
	}{
		{"accepted", 200, intPtr(200), "", outcomeAccepted},
		{"SIP rejected", 403, intPtr(403), "", outcomeRejected},
		{"business rejected", 200, intPtr(403), "", outcomeRejected},
		{"missing business result", 200, nil, "", outcomeUnknown},
		{"invalid then accepted", 200, intPtr(200), "invalid", outcomeAccepted},
		{"wrong source then accepted", 200, intPtr(200), "wrong-source", outcomeAccepted},
		{"provisional then accepted", 200, intPtr(200), "provisional", outcomeAccepted},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := runSIPCase(t, tc.status, tc.resultCode, tc.prefix)
			if got.Kind != tc.want {
				t.Fatalf("got=%+v want=%s", got, tc.want)
			}
		})
	}

	cfg := testConfig().SIP
	message := buildSIPMessage(cfg, 34567, "branch", "tag", "call", "含")
	parts := strings.SplitN(message, "\r\n\r\n", 2)
	if len(parts) != 2 || !strings.Contains(parts[0], "Content-Length: 3") || len([]byte(parts[1])) != 3 {
		t.Fatalf("UTF-8 content length not byte-based: %q", message)
	}
	if !strings.Contains(parts[0], "Via: SIP/2.0/UDP 127.0.0.1:34567;") ||
		!strings.Contains(parts[0], "From: <sip:1000001@127.0.0.1:5060>") {
		t.Fatal("Via must use the socket port while From preserves the configured SIP identity")
	}
}

func runSIPCase(t *testing.T, status int, resultCode *int, prefix string) sendOutcome {
	t.Helper()
	udp, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer udp.Close()
	cfg := testConfig().SIP
	cfg.Port = udp.LocalAddr().(*net.UDPAddr).Port
	done := make(chan struct{})
	go func() {
		defer close(done)
		packet := make([]byte, maxUDPResponse)
		n, peer, err := udp.ReadFromUDP(packet)
		if err != nil {
			return
		}
		header, _, ok := strings.Cut(string(packet[:n]), "\r\n\r\n")
		if !ok {
			return
		}
		headers := sipHeaders(header)
		if prefix == "invalid" {
			_, _ = udp.WriteToUDP([]byte("not SIP"), peer)
		}
		if prefix == "wrong-source" {
			forged, forgeErr := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
			if forgeErr == nil {
				writeSIPResponse(forged, peer, 403, intPtr(403), headers)
				_ = forged.Close()
			}
		}
		if prefix == "provisional" {
			writeSIPResponse(udp, peer, 180, nil, headers)
		}
		writeSIPResponse(udp, peer, status, resultCode, headers)
	}()
	got := sendAPPOINT(cfg, 500*time.Millisecond)
	<-done
	return got
}

func sipHeaders(header string) map[string]string {
	values := make(map[string]string)
	for _, line := range strings.Split(header, "\r\n")[1:] {
		name, value, ok := strings.Cut(line, ":")
		if ok {
			values[strings.ToLower(name)] = strings.TrimSpace(value)
		}
	}
	return values
}

func writeSIPResponse(conn *net.UDPConn, peer *net.UDPAddr, status int, resultCode *int, headers map[string]string) {
	body := ""
	if resultCode != nil {
		body = fmt.Sprintf("<params><resultCode>%d</resultCode></params>", *resultCode)
	}
	packet := fmt.Sprintf("SIP/2.0 %d Test\r\nVia: %s\r\nCall-ID: %s\r\nCSeq: %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n\r\n%s", status, headers["via"], headers["call-id"], headers["cseq"], len([]byte(body)), body)
	_, _ = conn.WriteToUDP([]byte(packet), peer)
}

func TestSIPTimeoutAndMalformedStatus(t *testing.T) {
	udp, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer udp.Close()
	cfg := testConfig().SIP
	cfg.Port = udp.LocalAddr().(*net.UDPAddr).Port
	got := make(chan sendOutcome, 1)
	go func() { got <- sendAPPOINT(cfg, 50*time.Millisecond) }()
	if _, _, err := udp.ReadFromUDP(make([]byte, maxUDPResponse)); err != nil {
		t.Fatal(err)
	}
	if result := <-got; result.Kind != outcomeUnknown {
		t.Fatalf("timeout result=%+v", result)
	}
	if _, ok := parseSIPResponse([]byte("SIP/2.0\r\n\r\n"), "branch", "call"); ok {
		t.Fatal("malformed status accepted")
	}
}

func TestSIPTransactionAndBodyValidation(t *testing.T) {
	base := []byte("SIP/2.0 200 OK\r\nVia: SIP/2.0/UDP 127.0.0.1:1;rport;branch=z9hG4bKbranch\r\nCall-ID: call\r\nCSeq: 20 MESSAGE\r\nContent-Length: 45\r\n\r\n<params><resultCode>200</resultCode></params>")
	for _, tc := range []struct {
		name   string
		mutate func([]byte) []byte
	}{
		{"call-id", func(packet []byte) []byte {
			return []byte(strings.Replace(string(packet), "Call-ID: call", "Call-ID: other", 1))
		}},
		{"cseq", func(packet []byte) []byte {
			return []byte(strings.Replace(string(packet), "CSeq: 20 MESSAGE", "CSeq: 21 MESSAGE", 1))
		}},
		{"branch", func(packet []byte) []byte {
			return []byte(strings.Replace(string(packet), "branch=z9hG4bKbranch", "branch=z9hG4bKother", 1))
		}},
		{"duplicate branch", func(packet []byte) []byte {
			return []byte(strings.Replace(string(packet), "branch=z9hG4bKbranch", "branch=z9hG4bKbranch;branch=other", 1))
		}},
		{"malformed status", func(packet []byte) []byte {
			return []byte(strings.Replace(string(packet), "200 OK", "2000 OK", 1))
		}},
		{"content length", func(packet []byte) []byte {
			return []byte(strings.Replace(string(packet), "Content-Length: 45", "Content-Length: 44", 1))
		}},
		{"duplicate result", func(packet []byte) []byte {
			return []byte(strings.Replace(string(packet), "</params>", "<resultCode>201</resultCode></params>", 1))
		}},
		{"malformed XML", func(packet []byte) []byte {
			return []byte(strings.Replace(string(packet), "<params><resultCode>200</resultCode></params>", "<params><resultCode>200</params>", 1))
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, ok := parseSIPResponse(tc.mutate(base), "branch", "call"); ok {
				t.Fatal("invalid response accepted")
			}
		})
	}
	if result, ok := parseSIPResponse(base, "branch", "call"); !ok || result.resultCode == nil || *result.resultCode != 200 {
		t.Fatalf("valid response rejected: %+v ok=%v", result, ok)
	}
	if result, ok := parseSIPResponse([]byte("SIP/2.0 200 OK\r\nVia: SIP/2.0/UDP 127.0.0.1:1;rport;branch=z9hG4bKbranch\r\nCall-ID: call\r\nCSeq: 20 MESSAGE\r\nContent-Length: 28\r\n\r\n<resultCode>200</resultCode>"), "branch", "call"); !ok || result.resultCode == nil || *result.resultCode != 200 {
		t.Fatalf("bare result root rejected: %+v ok=%v", result, ok)
	}
}

func TestResultCodeRequiresOneWellFormedValue(t *testing.T) {
	for _, body := range []string{
		"<params><resultCode>200</resultCode></params>",
		"<resultCode>200</resultCode>",
	} {
		code, ok := parseResultCode([]byte(body))
		if !ok || code == nil || *code != 200 {
			t.Fatalf("valid result rejected: %q", body)
		}
	}
	for _, body := range []string{
		"<params><resultCode>200</resultCode><resultCode>403</resultCode></params>",
		"<params><resultCode>200</params>",
		"<params><resultCode><nested>200</nested></resultCode></params>",
		"<params><resultCode>200</resultCode></params><extra/>",
		"<params><resultCode>invalid</resultCode></params>",
		"<params/>",
	} {
		if _, ok := parseResultCode([]byte(body)); ok {
			t.Fatalf("invalid result accepted: %q", body)
		}
	}
}

func TestUnknownStillReservesCooldownAndOversizedBody(t *testing.T) {
	service, err := newService(testConfig(), testToken, func(SIPConfig, time.Duration) sendOutcome {
		return sendOutcome{Kind: outcomeUnknown}
	}, time.Now)
	if err != nil {
		t.Fatal(err)
	}
	first := httptest.NewRecorder()
	service.Handler().ServeHTTP(first, requestFor("0123456789abcdef0123456789abcdef"))
	if first.Code != http.StatusGatewayTimeout || decodeResponse(t, first).Status != "unknown" {
		t.Fatalf("unknown response=%d %s", first.Code, first.Body.String())
	}
	second := httptest.NewRecorder()
	service.Handler().ServeHTTP(second, requestFor("abcdef0123456789abcdef0123456789"))
	if second.Code != http.StatusTooManyRequests || decodeResponse(t, second).Status != "cooldown" {
		t.Fatalf("unknown cooldown=%d %s", second.Code, second.Body.String())
	}

	oversized := httptest.NewRequest(http.MethodPost, "/api/elevator/down", strings.NewReader(`{"request_id":"0123456789abcdef0123456789abcdef","padding":"`+strings.Repeat("x", maxRequestBody)+`"}`))
	oversized.Header.Set("Authorization", "Bearer "+testToken)
	recorder := httptest.NewRecorder()
	service.Handler().ServeHTTP(recorder, oversized)
	if recorder.Code != http.StatusBadRequest || decodeResponse(t, recorder).Status != "error" {
		t.Fatalf("oversized=%d %s", recorder.Code, recorder.Body.String())
	}
}

func TestRequestIDDedupeExpiresAfterFiniteTTL(t *testing.T) {
	cfg := testConfig()
	cfg.CooldownSeconds = 1
	clock := time.Unix(1000, 0)
	calls := 0
	service, err := newService(cfg, testToken, func(SIPConfig, time.Duration) sendOutcome {
		calls++
		return sendOutcome{Kind: outcomeAccepted, SIPStatus: intPtr(200), ResultCode: intPtr(200)}
	}, func() time.Time { return clock })
	if err != nil {
		t.Fatal(err)
	}

	first := httptest.NewRecorder()
	service.Handler().ServeHTTP(first, requestFor("0123456789abcdef0123456789abcdef"))
	if first.Code != http.StatusOK {
		t.Fatalf("first status=%d", first.Code)
	}
	clock = clock.Add(2 * time.Second)
	second := httptest.NewRecorder()
	service.Handler().ServeHTTP(second, requestFor("abcdef0123456789abcdef0123456789"))
	if second.Code != http.StatusOK {
		t.Fatalf("second status=%d", second.Code)
	}
	repeat := httptest.NewRecorder()
	service.Handler().ServeHTTP(repeat, requestFor("0123456789abcdef0123456789abcdef"))
	if repeat.Code != http.StatusConflict {
		t.Fatalf("repeat status=%d", repeat.Code)
	}

	clock = clock.Add(61 * time.Second)
	expired := httptest.NewRecorder()
	service.Handler().ServeHTTP(expired, requestFor("0123456789abcdef0123456789abcdef"))
	if expired.Code != http.StatusOK {
		t.Fatalf("expired status=%d", expired.Code)
	}
	if calls != 3 {
		t.Fatalf("calls=%d, want 3", calls)
	}
}
