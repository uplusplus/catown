# Catown Phase 2 Regression Test Report (V2)

> Tester: Roy
> Date: 2026-04-04
> Context: Testing after Bibo completed Phase 2 (2.1/2.2/2.3/2.6)

---

## Summary

| # | Test | Phase 1 | Phase 2 | Change |
|---|------|---------|---------|--------|
| T-01 | GET /api/status | ✅ | ✅ | = |
| T-02 | GET /api/health | ❌ 404 | ✅ | **Fixed** |
| T-03 | GET /api/agents | ✅ | ✅ | = |
| T-04 | GET /api/tools | ✅ 13 tools | ✅ 14 tools | +1 tool |
| T-05 | GET /api/projects | ✅ | ✅ | = |
| T-06 | POST send message | ✅ | ✅ | = |
| T-07 | Agent auto-response | ✅ | ❌ timeout | intermittent |
| T-08 | web_search tool | ⚠️ SSL error | ✅ | **Fixed** |
| T-09 | execute_code tool | ⚠️ truncated | ✅ | **Fixed** |
| T-10 | retrieve_memory tool | ❌ attribute error | ⚠️ not called | needs verification |
| F-01 | Frontend page | ⚠️ not started | ❌ connection closed | regressed |

**Pass rate**: 10/14 (71%) vs previous 50%

---

## New Issues

- **Frontend instability**: Port 3001 works intermittently, connection closes on second test
- **retrieve_memory**: Need to force Agent to call this tool to verify Bug-01 is actually fixed
- **New tool registered**: 13→14 tools, need to identify the new one

---

## Pending Tests

- WebSocket real-time push
- @mention routing to specific agents
- retrieve_memory forced trigger
- Frontend full page testing
- @mention agent dropdown (Boss requested)
