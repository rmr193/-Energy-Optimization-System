# GridWise — 3-Minute Architecture & Solution Video Script
**BUP CSE Fest 2026 Hackathon · Smart Campus Energy Optimization**
**Duration**: Exactly 2 minutes 50 seconds (Target <= 3:00)

---

### [0:00 – 0:30] Introduction & Problem Understanding
**Visual**: Title slide with "GridWise — Smart Campus Energy Orchestrator", architecture banner, and BUP CSE Fest 2026 branding.
- **Presenter**: *"Hello judges and organizers! Welcome to our solution for the GridWise Smart Campus Energy Optimization Challenge at BUP CSE Fest 2026.
In modern smart campuses, energy costs fluctuate drastically throughout the day. We are tasked with optimizing a 24-hour horizon under time-of-use tariffs, solar availability, and battery storage constraints.
The core challenge lies in understanding unstructured natural-language operator notes—translating real-world operational changes like panel cleanings, maintenance windows, and emergency reserves into mathematical constraints without human intervention, while maintaining 100% schedule validity and end-of-day battery neutrality."*

---

### [0:30 – 1:15] System Architecture: From LLM to Math
**Visual**: Animated flow diagram showing:
`Operator Notes -> Multi-Provider LLM -> Deterministic Guardrails -> HiGHS LP Optimizer -> Replay Validator -> API Response`.
- **Presenter**: *"To solve this reliably, our architecture is built around a key principle from the problem statement: human notes are never directly trusted as math.
Our system operates in four distinct stages:
1. **LLM Directive Interpretation**: We leverage Google Gemini 2.5 Flash and OpenAI GPT-4o-mini with structured JSON output to extract directive types, whole-hour windows, and parameters. If an API key is absent or a network failure occurs, our deterministic local semantic engine seamlessly engages as a zero-crash fallback.
2. **Deterministic Guardrails**: We strictly sanitize model outputs—validating ascending hour arrays, clamping factor ranges between 0 and 1, validating battery capacities, and enforcing that irrelevant notes become explicit `no_op` entries.
3. **Exact Mathematical Optimizer**: Rather than using slow or approximate heuristics, we formulate the 24-hour schedule as a Linear Program and solve it using the state-of-the-art **HiGHS solver** via SciPy. This guarantees global mathematical cost optimality in under 5 milliseconds!
4. **Independent Replay Verification**: Before any response leaves our API, an independent verification engine replays the schedule hour-by-hour, confirming energy balance, battery bounds, and end-of-day neutrality."*

---

### [1:15 – 2:15] Live System Demo & Web Dashboard
**Visual**: Screen recording of the web dashboard at `http://localhost:8000`.
- **Presenter**: *"Let's see GridWise in action on our live interactive dashboard.
Here on the dashboard, we can select any of the 10 official public scenarios.
When we select **SAMPLE-01** and click 'Run Optimization Plan':
- The system interprets the solar washing note, reducing solar output to 25% between 12:00 and 14:00, while correctly marking the sports office note as `no_op`.
- Notice the **Energy Balance chart**: the battery charges during low-cost morning hours, discharges during the midday solar dip and evening peak tariff hours, and achieves the exact target cost of 38,365 BDT.
- Furthermore, looking at **SAMPLE-07**, where a 90 kWh emergency reserve and a 180 kWh transformer grid cap are simultaneously enforced, the HiGHS solver dynamically balances both constraints without a single violation.
- And crucially, end-of-day battery neutrality is strictly maintained at exactly 100% across all scenarios."*

---

### [2:15 – 2:45] Reliability, Benchmarks & Docker Fallback
**Visual**: Terminal output running `pytest -v` passing all 20 tests in under 5 seconds, followed by Docker container startup.
- **Presenter**: *"Our solution is thoroughly engineered for production reliability:
- We achieve a **100% pass rate** across all 10 public reference cases in automated test replay.
- Average endpoint latency is **under 15 milliseconds** with our local engine and **under 1.2 seconds** with remote LLM calls, well below the 5-second P95 threshold.
- The entire application is containerized in a clean, lightweight Docker image binding to `0.0.0.0:8000` with automated health checks at `/health` and zero hardcoded secrets."*

---

### [2:45 – 3:00] Conclusion
**Visual**: Summary slide with GitHub repository link, Docker command, and team credits.
- **Presenter**: *"GridWise combines the semantic understanding of state-of-the-art LLMs with the speed, precision, and safety of deterministic guardrails and mathematical linear programming.
Thank you for watching, and we look forward to the evaluation!"*
