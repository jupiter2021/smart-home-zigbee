package main

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"encoding/xml"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	defaultListenAddr  = "127.0.0.1:9998"
	defaultTimeout     = 3
	defaultCooldown    = 30
	defaultSIPPort     = 5060
	maxRequestBody     = 4096
	maxSeenRequests    = 1024
	maxUDPResponse     = 65535
	maxTokenLength     = 4096
	serverTimeoutSlack = 5 * time.Second
	serverMaxHeaders   = 8192
)

var (
	errInvalidConfig  = errors.New("invalid configuration")
	errInvalidRequest = errors.New("invalid request")
)

// SIPConfig contains only the fixed, already-authorized destination needed for
// the down-call. It deliberately has no fields for door access or floor grants.
type SIPConfig struct {
	SourceIP string
	SourceID string
	TargetIP string
	TargetID string
	Port     int
	Floor    int
	Family   int
}

type Config struct {
	ListenAddr      string
	TimeoutSeconds  int
	CooldownSeconds int
	SIP             SIPConfig
}

type configFile struct {
	ListenAddr      *string        `json:"listen_addr"`
	TimeoutSeconds  *int           `json:"timeout_seconds"`
	CooldownSeconds *int           `json:"cooldown_seconds"`
	SIP             *sipConfigFile `json:"sip"`
}

type sipConfigFile struct {
	SourceIP *string `json:"source_ip"`
	SourceID *string `json:"source_id"`
	TargetIP *string `json:"target_ip"`
	TargetID *string `json:"target_id"`
	Port     *int    `json:"port"`
	Floor    *int    `json:"floor"`
	Family   *int    `json:"family"`
}

// LoadConfig reads a strict JSON configuration and applies defaults only to
// omitted optional fields. It never opens a network socket.
func LoadConfig(path string) (Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("%w: read config", errInvalidConfig)
	}
	if err := rejectDuplicateJSONKeys(data); err != nil {
		return Config{}, fmt.Errorf("%w: parse config", errInvalidConfig)
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	var raw configFile
	if err := dec.Decode(&raw); err != nil {
		return Config{}, fmt.Errorf("%w: parse config", errInvalidConfig)
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		return Config{}, fmt.Errorf("%w: trailing config data", errInvalidConfig)
	}

	cfg := Config{
		ListenAddr:      defaultListenAddr,
		TimeoutSeconds:  defaultTimeout,
		CooldownSeconds: defaultCooldown,
	}
	if raw.ListenAddr != nil {
		cfg.ListenAddr = *raw.ListenAddr
	}
	if raw.TimeoutSeconds != nil {
		cfg.TimeoutSeconds = *raw.TimeoutSeconds
	}
	if raw.CooldownSeconds != nil {
		cfg.CooldownSeconds = *raw.CooldownSeconds
	}
	if raw.SIP == nil {
		return Config{}, fmt.Errorf("%w: sip is required", errInvalidConfig)
	}
	if raw.SIP.SourceIP != nil {
		cfg.SIP.SourceIP = *raw.SIP.SourceIP
	}
	if raw.SIP.SourceID != nil {
		cfg.SIP.SourceID = *raw.SIP.SourceID
	}
	if raw.SIP.TargetIP != nil {
		cfg.SIP.TargetIP = *raw.SIP.TargetIP
	}
	if raw.SIP.TargetID != nil {
		cfg.SIP.TargetID = *raw.SIP.TargetID
	}
	cfg.SIP.Port = defaultSIPPort
	if raw.SIP.Port != nil {
		cfg.SIP.Port = *raw.SIP.Port
	}
	if raw.SIP.Floor != nil {
		cfg.SIP.Floor = *raw.SIP.Floor
	}
	if raw.SIP.Family != nil {
		cfg.SIP.Family = *raw.SIP.Family
	}
	if err := validateConfig(cfg); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

// rejectDuplicateJSONKeys walks each JSON object independently. encoding/json
// otherwise keeps only the last value for duplicate keys during decoding.
func rejectDuplicateJSONKeys(data []byte) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	if err := walkJSONValue(dec); err != nil {
		return err
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		if err == nil {
			return errors.New("multiple JSON values")
		}
		return err
	}
	return nil
}

func walkJSONValue(dec *json.Decoder) error {
	token, err := dec.Token()
	if err != nil {
		return err
	}
	delim, isDelim := token.(json.Delim)
	if !isDelim {
		return nil
	}

	switch delim {
	case '{':
		seen := make(map[string]struct{})
		for dec.More() {
			keyToken, err := dec.Token()
			if err != nil {
				return err
			}
			key, ok := keyToken.(string)
			if !ok {
				return errors.New("invalid object key")
			}
			if _, exists := seen[key]; exists {
				return fmt.Errorf("duplicate object key %q", key)
			}
			seen[key] = struct{}{}
			if err := walkJSONValue(dec); err != nil {
				return err
			}
		}
		closeToken, err := dec.Token()
		if err != nil {
			return err
		}
		if closeToken != json.Delim('}') {
			return errors.New("invalid object")
		}
	case '[':
		for dec.More() {
			if err := walkJSONValue(dec); err != nil {
				return err
			}
		}
		closeToken, err := dec.Token()
		if err != nil {
			return err
		}
		if closeToken != json.Delim(']') {
			return errors.New("invalid array")
		}
	default:
		return errors.New("invalid JSON delimiter")
	}
	return nil
}

func validateConfig(cfg Config) error {
	if strings.TrimSpace(cfg.ListenAddr) == "" {
		return fmt.Errorf("%w: listen_addr", errInvalidConfig)
	}
	host, portText, err := net.SplitHostPort(cfg.ListenAddr)
	port, portErr := strconv.Atoi(portText)
	if err != nil || portErr != nil || !validListenIPv4(host) || port < 1 || port > 65535 {
		return fmt.Errorf("%w: listen_addr", errInvalidConfig)
	}
	if cfg.TimeoutSeconds < 1 || cfg.CooldownSeconds < 1 {
		return fmt.Errorf("%w: timeout/cooldown", errInvalidConfig)
	}
	// Avoid a time.Duration overflow while still accepting ordinary positive
	// integer settings.
	maxSeconds := int64((time.Duration(1<<63-1) - serverTimeoutSlack) / time.Second)
	if int64(cfg.TimeoutSeconds) > maxSeconds || int64(cfg.CooldownSeconds) > maxSeconds {
		return fmt.Errorf("%w: timeout/cooldown range", errInvalidConfig)
	}
	if !validIPv4(cfg.SIP.SourceIP) || !validIPv4(cfg.SIP.TargetIP) {
		return fmt.Errorf("%w: SIP addresses", errInvalidConfig)
	}
	if !validSIPIdentity(cfg.SIP.SourceID) || !validSIPIdentity(cfg.SIP.TargetID) {
		return fmt.Errorf("%w: SIP identities", errInvalidConfig)
	}
	if cfg.SIP.Port < 1 || cfg.SIP.Port > 65535 || cfg.SIP.Floor < 1 || cfg.SIP.Family < 1 {
		return fmt.Errorf("%w: SIP values", errInvalidConfig)
	}
	return nil
}

func validListenIPv4(value string) bool {
	ip := net.ParseIP(value)
	return ip != nil && ip.To4() != nil && !ip.IsUnspecified() && !ip.IsMulticast() && !ip.Equal(net.IPv4bcast) && ip.String() == value
}

func validIPv4(value string) bool {
	ip := net.ParseIP(value)
	return ip != nil && ip.To4() != nil && !ip.IsUnspecified() && !ip.IsMulticast() && !ip.Equal(net.IPv4bcast) && ip.String() == value
}

func validSIPIdentity(value string) bool {
	if value == "" || len(value) > 64 {
		return false
	}
	for _, r := range value {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

type request struct {
	RequestID string `json:"request_id"`
}

type response struct {
	RequestID         string `json:"request_id"`
	Status            string `json:"status"`
	Message           string `json:"message"`
	SIPStatus         *int   `json:"sip_status,omitempty"`
	ResultCode        *int   `json:"result_code,omitempty"`
	RetryAfterSeconds *int   `json:"retry_after_seconds,omitempty"`
}

type outcomeKind string

const (
	outcomeAccepted outcomeKind = "accepted"
	outcomeRejected outcomeKind = "rejected"
	outcomeUnknown  outcomeKind = "unknown"
	outcomeError    outcomeKind = "error"
)

type sendOutcome struct {
	Kind       outcomeKind
	SIPStatus  *int
	ResultCode *int
}

type sendFunc func(SIPConfig, time.Duration) sendOutcome

type seenRequest struct {
	at time.Time
}

type Service struct {
	cfg   Config
	token [sha256.Size]byte
	send  sendFunc
	now   func() time.Time

	mu          sync.Mutex
	attempted   bool
	inFlight    bool
	lastAttempt time.Time
	seen        map[string]seenRequest
}

func NewService(cfg Config, token string) (*Service, error) {
	return newService(cfg, token, sendAPPOINT, time.Now)
}

func newService(cfg Config, token string, sender sendFunc, now func() time.Time) (*Service, error) {
	if err := validateConfig(cfg); err != nil || !validToken(token) || sender == nil || now == nil {
		return nil, errInvalidConfig
	}
	digest := sha256.Sum256([]byte(token))
	return &Service{
		cfg:   cfg,
		token: digest,
		send:  sender,
		now:   now,
		seen:  make(map[string]seenRequest),
	}, nil
}

func validToken(token string) bool {
	if len(token) < 32 || len(token) > maxTokenLength {
		return false
	}
	for i := 0; i < len(token); i++ {
		if token[i] < 0x21 || token[i] > 0x7e {
			return false
		}
	}
	return true
}

func (s *Service) Handler() http.Handler {
	return http.HandlerFunc(s.serveHTTP)
}

func (s *Service) serveHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/api/elevator/down" {
		writeResponse(w, http.StatusNotFound, response{Status: "rejected", Message: "not found"})
		return
	}
	if r.Method != http.MethodPost {
		writeResponse(w, http.StatusMethodNotAllowed, response{Status: "rejected", Message: "method not allowed"})
		return
	}
	if !s.authorized(r.Header.Get("Authorization")) {
		writeResponse(w, http.StatusUnauthorized, response{Status: "error", Message: "unauthorized"})
		return
	}
	req, err := decodeRequest(r)
	if err != nil {
		writeResponse(w, http.StatusBadRequest, response{Status: "error", Message: "invalid request"})
		return
	}
	if !validRequestID(req.RequestID) {
		writeResponse(w, http.StatusBadRequest, response{Status: "error", Message: "invalid request"})
		return
	}

	resp, status := s.execute(req.RequestID)
	writeResponse(w, status, resp)
}

func (s *Service) authorized(value string) bool {
	if !strings.HasPrefix(value, "Bearer ") || len(value) <= len("Bearer ") {
		return false
	}
	digest := sha256.Sum256([]byte(value[len("Bearer "):]))
	return subtle.ConstantTimeCompare(s.token[:], digest[:]) == 1
}

func decodeRequest(r *http.Request) (request, error) {
	if r.Body == nil {
		return request{}, errInvalidRequest
	}
	if r.ContentLength > maxRequestBody {
		return request{}, errInvalidRequest
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, maxRequestBody+1))
	if err != nil || len(body) > maxRequestBody {
		return request{}, errInvalidRequest
	}
	dec := json.NewDecoder(bytes.NewReader(body))
	var req request
	first, err := dec.Token()
	if err != nil {
		return request{}, errInvalidRequest
	}
	delim, ok := first.(json.Delim)
	if !ok || delim != '{' {
		return request{}, errInvalidRequest
	}
	seenRequestID := false
	for dec.More() {
		keyToken, err := dec.Token()
		if err != nil {
			return request{}, errInvalidRequest
		}
		key, ok := keyToken.(string)
		if !ok || key != "request_id" || seenRequestID {
			return request{}, errInvalidRequest
		}
		if err := dec.Decode(&req.RequestID); err != nil {
			return request{}, errInvalidRequest
		}
		seenRequestID = true
	}
	last, err := dec.Token()
	if err != nil {
		return request{}, errInvalidRequest
	}
	closeDelim, ok := last.(json.Delim)
	if !ok || closeDelim != '}' || !seenRequestID {
		return request{}, errInvalidRequest
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		return request{}, errInvalidRequest
	}
	return req, nil
}

func validRequestID(value string) bool {
	if len(value) != 32 {
		return false
	}
	for _, char := range value {
		if (char < '0' || char > '9') && (char < 'a' || char > 'f') {
			return false
		}
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func (s *Service) execute(requestID string) (response, int) {
	now := s.now()
	s.mu.Lock()
	s.pruneSeenLocked(now)
	if _, exists := s.seen[requestID]; exists {
		s.mu.Unlock()
		return response{RequestID: requestID, Status: "duplicate", Message: "duplicate request"}, http.StatusConflict
	}
	if s.inFlight || (s.attempted && now.Sub(s.lastAttempt) < s.cooldownDuration()) {
		retry := s.retryAfterSeconds(now)
		s.mu.Unlock()
		return response{
			RequestID:         requestID,
			Status:            "cooldown",
			Message:           "cooldown active",
			RetryAfterSeconds: &retry,
		}, http.StatusTooManyRequests
	}
	// Reserve both the dedupe key and the cooldown before sending. This keeps
	// concurrent requests from producing multiple physical calls.
	s.seen[requestID] = seenRequest{at: now}
	s.attempted = true
	s.inFlight = true
	s.lastAttempt = now
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		s.inFlight = false
		s.mu.Unlock()
	}()

	out := s.send(s.cfg.SIP, time.Duration(s.cfg.TimeoutSeconds)*time.Second)
	switch out.Kind {
	case outcomeAccepted:
		return response{RequestID: requestID, Status: "accepted", Message: "request accepted", SIPStatus: out.SIPStatus, ResultCode: out.ResultCode}, http.StatusOK
	case outcomeRejected:
		return response{RequestID: requestID, Status: "rejected", Message: "request rejected", SIPStatus: out.SIPStatus, ResultCode: out.ResultCode}, http.StatusUnprocessableEntity
	case outcomeUnknown:
		return response{RequestID: requestID, Status: "unknown", Message: "request result unknown", SIPStatus: out.SIPStatus, ResultCode: out.ResultCode}, http.StatusGatewayTimeout
	default:
		return response{RequestID: requestID, Status: "error", Message: "request failed", SIPStatus: out.SIPStatus, ResultCode: out.ResultCode}, http.StatusInternalServerError
	}
}

func (s *Service) retryAfterSeconds(now time.Time) int {
	remaining := s.cooldownDuration() - now.Sub(s.lastAttempt)
	if remaining <= 0 {
		return 1
	}
	retry := int((remaining + time.Second - 1) / time.Second)
	if retry < 1 {
		return 1
	}
	return retry
}

func (s *Service) cooldownDuration() time.Duration {
	return time.Duration(s.cfg.CooldownSeconds) * time.Second
}

func (s *Service) dedupeDuration() time.Duration {
	d := s.cooldownDuration()
	if d < time.Minute {
		return time.Minute
	}
	return d
}

func (s *Service) pruneSeenLocked(now time.Time) {
	cutoff := now.Add(-s.dedupeDuration())
	for id, item := range s.seen {
		if item.at.Before(cutoff) {
			delete(s.seen, id)
		}
	}
	for len(s.seen) > maxSeenRequests {
		var oldestID string
		var oldest time.Time
		for id, item := range s.seen {
			if oldestID == "" || item.at.Before(oldest) {
				oldestID, oldest = id, item.at
			}
		}
		if oldestID == "" {
			break
		}
		delete(s.seen, oldestID)
	}
}

func writeResponse(w http.ResponseWriter, status int, resp response) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(resp)
}

func main() {
	configPath := flag.String("config", "config.json", "path to the elevator configuration")
	flag.Parse()
	cfg, err := LoadConfig(*configPath)
	if err != nil {
		log.Print("configuration invalid")
		os.Exit(1)
	}
	service, err := NewService(cfg, os.Getenv("SMZ_ELEVATOR_TOKEN"))
	if err != nil {
		log.Print("configuration invalid")
		os.Exit(1)
	}
	serverTimeout := time.Duration(cfg.TimeoutSeconds)*time.Second + serverTimeoutSlack
	server := &http.Server{
		Addr:              cfg.ListenAddr,
		Handler:           service.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       serverTimeout,
		WriteTimeout:      serverTimeout,
		IdleTimeout:       30 * time.Second,
		MaxHeaderBytes:    serverMaxHeaders,
	}
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Print("server stopped")
		os.Exit(1)
	}
}

func sendAPPOINT(cfg SIPConfig, timeout time.Duration) sendOutcome {
	body := fmt.Sprintf("<params>\n\t<to>sip:%s@%s:%d</to>\n\t<elev>0</elev>\n\t<direct>2</direct>\n\t<floor>%d</floor>\n\t<family>%d</family>\n\t<app>elev</app>\n\t<event>appoint</event>\n\t<event_url>/elev/appoint</event_url>\n</params>\n", cfg.TargetID, cfg.TargetIP, cfg.Port, cfg.Floor, cfg.Family)
	branch, err := randomToken(16)
	if err != nil {
		return sendOutcome{Kind: outcomeError}
	}
	tag, err := randomToken(16)
	if err != nil {
		return sendOutcome{Kind: outcomeError}
	}
	callID, err := randomToken(16)
	if err != nil {
		return sendOutcome{Kind: outcomeError}
	}
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP(cfg.SourceIP).To4(), Port: 0})
	if err != nil {
		return sendOutcome{Kind: outcomeError}
	}
	defer conn.Close()
	local := conn.LocalAddr().(*net.UDPAddr)
	msg := buildSIPMessage(cfg, local.Port, branch, tag, callID, body)
	dst := &net.UDPAddr{IP: net.ParseIP(cfg.TargetIP).To4(), Port: cfg.Port}
	deadline := time.Now().Add(timeout)
	if err := conn.SetDeadline(deadline); err != nil {
		return sendOutcome{Kind: outcomeError}
	}
	if _, err := conn.WriteToUDP([]byte(msg), dst); err != nil {
		return sendOutcome{Kind: outcomeError}
	}
	packet := make([]byte, maxUDPResponse)
	for {
		n, peer, err := conn.ReadFromUDP(packet)
		if err != nil {
			if errors.Is(err, os.ErrDeadlineExceeded) || errors.Is(err, net.ErrClosed) {
				return sendOutcome{Kind: outcomeUnknown}
			}
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				return sendOutcome{Kind: outcomeUnknown}
			}
			return sendOutcome{Kind: outcomeUnknown}
		}
		targetIP := net.ParseIP(cfg.TargetIP).To4()
		if peer == nil || !peer.IP.Equal(targetIP) || peer.Port != cfg.Port {
			continue
		}
		parsed, ok := parseSIPResponse(packet[:n], branch, callID)
		if !ok {
			continue
		}
		if parsed.status >= 100 && parsed.status < 200 {
			continue
		}
		if parsed.status >= 300 {
			return sendOutcome{Kind: outcomeRejected, SIPStatus: intPtr(parsed.status), ResultCode: parsed.resultCode}
		}
		if parsed.status < 200 {
			continue
		}
		if parsed.resultCode == nil {
			return sendOutcome{Kind: outcomeUnknown, SIPStatus: intPtr(parsed.status)}
		}
		if *parsed.resultCode == 200 {
			return sendOutcome{Kind: outcomeAccepted, SIPStatus: intPtr(parsed.status), ResultCode: parsed.resultCode}
		}
		return sendOutcome{Kind: outcomeRejected, SIPStatus: intPtr(parsed.status), ResultCode: parsed.resultCode}
	}
}

func randomToken(size int) (string, error) {
	b := make([]byte, size)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

func buildSIPMessage(cfg SIPConfig, sourcePort int, branch, tag, callID, body string) string {
	return fmt.Sprintf("MESSAGE sip:%s@%s:%d SIP/2.0\r\n"+
		"Via: SIP/2.0/UDP %s:%d;rport;branch=z9hG4bK%s\r\n"+
		"From: <sip:%s@%s:%d>;tag=%s\r\n"+
		"To: <sip:%s@%s:%d>\r\n"+
		"Call-ID: %s\r\n"+
		"CSeq: 20 MESSAGE\r\n"+
		"Content-Type: text/plain\r\n"+
		"Max-Forwards: 70\r\n"+
		"User-Agent: DnakeVoip v1.0\r\n"+
		"Content-Length: %d\r\n"+
		"\r\n%s",
		cfg.TargetID, cfg.TargetIP, cfg.Port,
		cfg.SourceIP, sourcePort, branch,
		cfg.SourceID, cfg.SourceIP, cfg.Port, tag,
		cfg.TargetID, cfg.TargetIP, cfg.Port,
		callID, len([]byte(body)), body)
}

type parsedSIPResponse struct {
	status     int
	resultCode *int
}

func parseSIPResponse(packet []byte, branch, callID string) (parsedSIPResponse, bool) {
	separator := []byte("\r\n\r\n")
	idx := bytes.Index(packet, separator)
	if idx < 0 {
		return parsedSIPResponse{}, false
	}
	headers := strings.Split(string(packet[:idx]), "\r\n")
	if len(headers) < 2 || !strings.HasPrefix(headers[0], "SIP/2.0 ") {
		return parsedSIPResponse{}, false
	}
	statusLine := strings.SplitN(headers[0], " ", 3)
	if len(statusLine) != 3 || len(statusLine[1]) != 3 {
		return parsedSIPResponse{}, false
	}
	for _, digit := range statusLine[1] {
		if digit < '0' || digit > '9' {
			return parsedSIPResponse{}, false
		}
	}
	status, err := strconv.Atoi(statusLine[1])
	if err != nil || status < 100 || status > 699 {
		return parsedSIPResponse{}, false
	}
	values := make(map[string]string, len(headers)-1)
	for _, line := range headers[1:] {
		colon := strings.IndexByte(line, ':')
		if colon <= 0 {
			return parsedSIPResponse{}, false
		}
		name := strings.ToLower(strings.TrimSpace(line[:colon]))
		value := strings.TrimSpace(line[colon+1:])
		if name == "" || value == "" {
			return parsedSIPResponse{}, false
		}
		if _, exists := values[name]; exists {
			return parsedSIPResponse{}, false
		}
		values[name] = value
	}
	contentLength, err := strconv.Atoi(values["content-length"])
	if err != nil || contentLength < 0 || idx+len(separator)+contentLength != len(packet) {
		return parsedSIPResponse{}, false
	}
	if values["call-id"] != callID || !validCSeq(values["cseq"]) || !validViaBranch(values["via"], branch) {
		return parsedSIPResponse{}, false
	}
	body := packet[idx+len(separator):]
	resultCode, hasResult := parseResultCode(body)
	if !hasResult {
		resultCode = nil
	}
	return parsedSIPResponse{status: status, resultCode: resultCode}, true
}

func validCSeq(value string) bool {
	parts := strings.Fields(value)
	return len(parts) == 2 && parts[0] == "20" && strings.EqualFold(parts[1], "MESSAGE")
}

func validViaBranch(value, branch string) bool {
	parts := strings.Split(value, ";")
	if !strings.HasPrefix(parts[0], "SIP/2.0/UDP ") {
		return false
	}
	found := false
	for _, part := range parts[1:] {
		keyValue := strings.SplitN(strings.TrimSpace(part), "=", 2)
		if len(keyValue) == 2 && strings.EqualFold(keyValue[0], "branch") {
			if found || keyValue[1] != "z9hG4bK"+branch {
				return false
			}
			found = true
		}
	}
	return found
}

func parseResultCode(body []byte) (*int, bool) {
	decoder := xml.NewDecoder(bytes.NewReader(body))
	count, depth := 0, 0
	rootSeen, rootClosed := false, false
	resultDepth := 0
	var resultText strings.Builder
	for {
		token, err := decoder.Token()
		if err == io.EOF {
			if !rootSeen || !rootClosed || depth != 0 || count != 1 || resultDepth != 0 {
				return nil, false
			}
			value, err := strconv.Atoi(strings.TrimSpace(resultText.String()))
			if err != nil {
				return nil, false
			}
			return intPtr(value), true
		}
		if err != nil {
			return nil, false
		}
		switch item := token.(type) {
		case xml.StartElement:
			if depth == 0 {
				if rootClosed {
					return nil, false
				}
				rootSeen = true
			}
			if resultDepth != 0 {
				return nil, false
			}
			depth++
			if item.Name.Local != "resultCode" {
				continue
			}
			count++
			if count > 1 {
				return nil, false
			}
			resultDepth = depth
		case xml.EndElement:
			if depth == 0 {
				return nil, false
			}
			if resultDepth == depth {
				resultDepth = 0
			}
			depth--
			if depth == 0 {
				rootClosed = true
			}
		case xml.CharData:
			if depth == 0 && strings.TrimSpace(string(item)) != "" {
				return nil, false
			}
			if resultDepth != 0 {
				resultText.Write([]byte(item))
			}
		}
	}
}

func intPtr(value int) *int {
	return &value
}
