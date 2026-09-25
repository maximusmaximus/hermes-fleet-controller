---
name: trollbox-mining-review
description: Architectural auditing, protocol analysis, and code review methodology for the Trollbox browser-based blockchain mining stack (WASM RandomSFX, Stratum WebSocket fan-in, PPLNS pool proxy, Safex RPC).
---

# Trollbox Mining Stack — Code Review & Protocol Audit Guide

This skill equips the agent with domain-specific knowledge of the **Trollbox Mining Stack**, an end-to-end browser cryptocurrency mining pipeline utilizing WebAssembly (RandomSFX/RandomX), Stratum-over-WebSocket fan-in, PPLNS micro-share proxying, and Cryptonote RPC daemon integration.

## 1. System Architecture

```text
Browser Miner (WASM RandomSFX)
       │ WSS (Port 8443 / Cloudflare)
       ▼
Caddy Reverse Proxy (TLS termination, WSS pass-through)
       │ TCP / WS (127.0.0.1:18445)
       ▼
stratum-ws.mjs (Fan-in: WebSocket frame decoder/encoder, line buffering)
       │ Stratum TCP (127.0.0.1:3333)
       ▼
stratum-proxy.js (PPLNS proxy: micro-share validation, diff calculation, native HTTP digest RPC client)
       │ HTTP Digest Auth (17402)
       ▼
safexd Daemon (Cryptonote JSON-RPC: getblocktemplate, submitblock, get_info)
```

## 2. Core Components & Responsibilities

### A. fan-in/stratum-ws.mjs
- Accepts WebSocket connections from web browsers (RFC 6455 framing).
- Decodes masked client frames (Opcode 0x01 text, 0x08 close, 0x09 ping, 0x0a pong).
- Opens a dedicated TCP connection to stratum-proxy.js on 127.0.0.1:3333.
- Ensures line-buffering: splits TCP incoming chunks on newline to prevent JSON-RPC fragmentation across frames.
- Sends heartbeat ping frames every 30s to keep cloudflare tunnels alive.

### B. pool/stratum-proxy.js
- Assigns reachable share difficulty to browser miners (POOL_SHARE_DIFF, default 512).
- Converts diff to 32-byte target buffer via diffToTarget32() and calculates 4-byte LE compact target via networkTarget4().
- Validates submitted hashes against targets:
  - Micro-shares: If hash meets shareDiff, increments SHARE_COUNT, replies {status: "OK", block: false}, and avoids spamming submitblock.
  - Block wins: If hash meets network difficulty, executes submitblock via HTTP digest auth to safexd.
- Implements buildDigestAuth(): MD5 HA1/HA2 challenge-response generation without external dependencies.
- Enforces rate limiting on submitblock (max 1 per 2000ms).

### C. chain-api.js
- Public-facing HTTP REST API for chain stats, headers, and health checks on port 18446.
- Restricts JSON-RPC methods via allowlist: get_info, get_last_block_header, get_block_headers_range, etc.
- In-memory cache for get_info with 10s TTL to protect daemon RPC from stampedes.

## 3. High-Priority Code Review Vectors & Improvements

### 1. Insecure Process Execution in chain-api.js (CRITICAL)
- Current Issue: chain-api.js executes curl via child_process.exec(cmd) with string interpolation.
- Risks: Command injection, process spawning overhead, credential leakage in process tables.
- Improvement: Replace child_process.exec(curl ...) with native Node.js http.request using digest auth already built in stratum-proxy.js.

### 2. Hardcoded Passwords and Credentials (HIGH)
- Current Issue: chain-api.js and stratum-proxy.js have hardcoded credentials fallback.
- Improvement: Strictly require environment variables or .env files, failing gracefully on startup if missing.

### 3. Backpressure & Socket Teardown in stratum-ws.mjs (MEDIUM)
- Current Issue: When browser disconnects abruptly or encounters network jitter, TCP connections to pool proxy may remain in CLOSE_WAIT or leak memory.
- Improvement: Attach explicit socket.on('close'), socket.on('error'), poolSock.on('close'), and implement backpressure checks.

### 4. Template & Job Refresh Broadcast (MEDIUM)
- Current Issue: If multiple miners connect, refreshing jobs sequentially causes micro-bursts of RPC requests.
- Improvement: Implement centralized JobManager broadcasting new jobs to all miners simultaneously.

### 5. Browser Mining Client Ergonomics (MEDIUM)
- Auto-detect navigator.hardwareConcurrency and default to concurrency - 1 to keep browser UI responsive.
- Listen for document.visibilityState == 'hidden' to throttle hash rates.
- Ensure WASM memory alignment (64-byte aligned buffers) for vector/SIMD operations.

## 4. Verification & Testing Procedure

Always run the full test suite after proposing changes:
```bash
cd /opt/fleet/shared-workspace/trollbox-mining-stack
./scripts/check-all.sh
```
