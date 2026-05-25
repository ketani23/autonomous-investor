# Project Charter v2: Autonomous Investor

**Author:** Aniket
**Status:** Living document; revise as understanding evolves
**Last updated:** Phase 0

---

## 1. What this project is

This is an AI systems engineering project that uses autonomous macro investing as its substrate. The deliverable is not a profitable trading system. The deliverable is a body of concrete observations and defended opinions about how to design, evaluate, and operate LLM-based agent systems that make consequential, judgment-laden decisions in a domain with a brutal, indifferent ground truth.

The project is also explicitly an artifact in service of an interview at AIA Labs (Bridgewater). The architecture, vocabulary, and evaluation choices are calibrated to engage with AIA's stated worldview — causal reasoning, bitemporal modeling, explainability-as-capability, deploy-with-real-stakes — at conversation depth.

Investing is chosen as the domain for three reasons: (1) the ground truth signal is unforgiving and unfakeable, (2) it maps directly to the work AIA is doing, and (3) it forces the agent system to confront problems — calibration, memory, robustness, adversarial inputs, judgment under uncertainty, non-stationarity, foreknowledge contamination — that matter across all serious agent applications.

## 2. What this project is not

- It is **not** an attempt to build a profitable trading strategy.
- It is **not** a demonstration of finance knowledge. The author is not a quant and is not pretending to be one. Finance fluency is sufficient to engage AIA's vocabulary without embarrassment; depth lives in the AI systems layer.
- It is **not** a polished product. It is a research substrate.
- It is **not** a benchmark of LLM capability. It is a study of agent architecture, instrumentation, and evaluation.

## 3. The reframe (load-bearing)

The natural framing of "build an autonomous investor" pulls toward finance: better strategies, better backtests, better risk models. That framing is wrong for this project. The author's edge and the project's interview value lie one layer up: in _how_ the agent system thinks, learns, disagrees with itself, and knows what it doesn't know.

The substrate (data, broker, risk gateway, backtester) exists to make the agent's decisions real. It is engineered to be _adequate_, not impressive. Time spent improving the substrate beyond adequacy is time stolen from the project's actual purpose.

The investment loop the project uses, deliberately mirroring AIA's manifesto language, is: **Perceive → Hypothesize → Investigate → Synthesize → Learn**.

## 4. The five questions the project must answer

By the end of the project, the author must be able to answer these five questions with concrete specifics drawn from this project — not generic observations:

1. What did you build, architecturally?
2. How did you evaluate it? (Including: how did you handle foreknowledge bias, calibration, and trajectory-level scoring?)
3. **Where does the harness end and the weights begin in your system, and how did your view on that boundary shift as you built it?**
4. **What did you observe about causal vs. correlational reasoning in your agent, and how did you encode causal structure where it mattered?**
5. What would you do differently if you were building this at AIA scale — specifically, what would you keep from AIA's apparent design, what would you change, and why?

Every phase plan is in service of these questions. Activities that don't move at least one of them forward are deprioritized.

## 5. Architectural principles

These are invariants. Claude Code should treat them as constraints, not suggestions.

**Substrate is frozen after Phase 1.** The data layer, the broker adapter, the backtest harness, and the storage schema are built fast in Phase 1 and frozen except for bug fixes. Wanting to improve the substrate after Phase 1 is a sign to write down a "known limitation" and move on.

**Bitemporal data, not point-in-time.** Every datum has two timestamps: the _valid-from_ date (when the fact became true in the world) and the _as-of_ date (when the system learned the fact). Restatements create new (as-of, valid-from) tuples without overwriting prior rows. Any agent decision must be reconstructable using exactly the information the system had at decision time, even if the underlying data has since been revised. This is non-negotiable and mirrors AIA's stated guardrail invariant.

**Causal reasoning is a first-class concern.** The agent system reasons explicitly over small causal DAGs of macro variables — not just statistical patterns. Hypotheses are expressed as causal claims with directionality. Where possible, the system distinguishes observational ("X tends to coincide with Y") from interventional ("if X changed, Y would respond") reasoning. The project does not require the author to become a causal-inference researcher, but the agent's outputs and the evaluation harness must engage with this distinction concretely.

**Instrumentation precedes the thing being instrumented.** Every agent invocation produces a structured trace record before any agent does anything interesting. Retrofitting logging is forbidden. Traces are bitemporal-aware: they record what the agent saw at decision time, not what is true now.

**Two architectures done well beats four done shallowly.** The project produces exactly two agent architectures, run in parallel against the same eval scenarios.

**Live P&L is a footnote, not a metric.** Once live, the author does not watch P&L. The risk gateway watches it. If the system is producing learnings, the project is succeeding regardless of returns.

**The agent eval harness is the highest-value artifact.** More than the architectures, more than the live deployment, more than any individual observation. If only one artifact could survive the project, it would be this. The harness must engage with foreknowledge bias detection, calibration, trajectory-level scoring, and inter-judge reliability.

**Deterministic where it must be, agentic where it should be.** Risk limits, order routing, accounting, reconciliation, and the trade gate are deterministic code. Hypothesis generation, research synthesis, causal reasoning, and decision-framing are the agent's job. The boundary is non-negotiable.

**Memory and principles are first-class concerns, not decoration.** Whether and how an agent accumulates a worldview across runs — including a decision journal, a curated principles document, a strategy graveyard, and a calibration history — is one of the project's central questions.

**Architecture B is built faithfully to AIA's apparent design — and then critiqued from inside.** The point of Architecture B is not to clone AIA, but to live inside their architecture long enough to develop a defensible "what I'd do differently if I joined" point of view. The architecture is built as the author understands AIA's stack to work (Planner → Supervisor → Specialists, with supervisor reconciliation and statistical calibration). Then the author surfaces, in the debrief, where it broke down, what felt forced, and what they'd change.

## 6. Operating constraints

**Runtime and access:**

- **Max 20x subscription** ($200/mo) provides Claude Code for development and a $200/mo Agent SDK credit pool for the runtime agent.
- **Runtime agent built on the Claude Agent SDK**, not the raw API. Same agent loop as Claude Code, programmable in Python, on subscription billing.
- **Model selection inside the agent:** Haiku for routine tool work, Sonnet for most reasoning, Opus reserved for the supervisor and the deep-research steps. Document model selection per role and treat it as a design variable.
- **Usage credits enabled with a hard cap** (~$50/mo) as overflow insurance. The risk gateway enforces a token budget per decision as a secondary check.

**Libraries to use:**

- `vectorbt` for backtesting. Wrapped, not extended.
- Alpaca for paper and live brokerage. No premature abstraction over brokers.
- Postgres for all persistent state. Schema must support bitemporal queries.
- FRED for macro data; Sharadar or financialdatasets.ai for fundamentals if needed.
- Claude Agent SDK as the agent runtime.

**Libraries deferred or staged:**

- **LangGraph.** Hand-roll the orchestration in plain Python for Architectures A and B. The author needs to see the wires for interview value. _In Phase 5, optionally build a parallel LangGraph version of Architecture B_ to develop an opinion on what LangGraph hides, what it makes easier, and whether AIA's use of it is load-bearing or replaceable.
- **Other orchestration frameworks (LangChain, AutoGen, CrewAI).** Avoid throughout.
- **Streaming infrastructure (Kafka, etc.).** Out of scope. This is a daily-decision system.

**Risk gateway invariants (mirroring AIA's apparent trade gate):**

- Every order, from any source (backtest, paper, live, manual), passes through the gateway.
- Hard caps on per-position size, gross/net exposure, daily loss, order frequency, instrument whitelist.
- Two-key approval for any non-trivial position change (model proposal + author signature) recorded immutably with bitemporal timestamps.
- Tiered permissioning: read-only research agents and portfolio-touching agents are separated at the code boundary, not just by convention.
- Model-version pinning at the orchestration layer; no silent model upgrades between decisions.
- Kill-switch at the portfolio level that halts new orders and optionally flattens positions.
- Limits and configuration are version-controlled.
- Exhaustive unit tests written before any agent connects.

**Universe and cadence:**

- ~15 liquid ETFs across equity, duration, credit, commodity, FX sleeves.
- Agent thinks daily (Perceive/Investigate may run more often). Portfolio rebalances weekly. This decoupling is intentional.

**Capital:**

- Paper trading from Phase 2.
- Small live capital ($1–2k) from Phase 4, in parallel with paper.
- Scale-up only if the project's intellectual core is on track; not based on returns.

**Repository:**

- Single public Git repo on the author's local machine.
- Credentials in `.env`, never committed. `.gitignore` in place from commit one.
- Commit history is part of the artifact. Write commit messages with the future reader (interviewer) in mind.

## 7. Phase structure

Phases are milestone-based, not calendar-based. Some phases may compress to days; others may take longer than estimated, especially the observation phases. The debrief cadence is "after each phase," not weekly.

**Phase 0 — Pre-work (~1 week of background reading, parallel with Phase 1 if desired).**
A short, deliberate reading list to make the project's vocabulary native:

- _AIA Labs page_ (bridgewater.com/aia-labs) — read twice, internalize the language.
- _AIA Forecaster paper_ (arXiv 2511.07678) — focus on §3 (architecture), §5 (search ablations), §6 (calibration), §7 (foreknowledge). Skim Appendix B.
- _AWS re:Invent 2024 FSI202_ (Linsky's slides) — architecture stack, blueprints → agents migration, named specialists.
- _Pearl primer at conversation depth_: chapters of _The Book of Why_ on the three rungs and do-calculus; Sekhon's Wikipedia entry; abstract of the Künzel/Sekhon meta-learners paper.
- _López de Prado's Advances in Financial Machine Learning_, chapters on purged k-fold, embargo, CPCV, deflated Sharpe — for fluency, not implementation.

**Phase 1 — Substrate + monolithic agent end-to-end.**
Goal: a single Claude Agent SDK agent runs daily, perceives market and macro data, hypothesizes, investigates, synthesizes a proposed portfolio, the risk gateway evaluates it, and the decision is logged with full bitemporal traces. No execution yet. Substrate frozen at phase end.

**Phase 2 — Eval harness v1 + memory v0 + paper execution.**
Goal: 5–8 eval scenarios across historical replays, synthetic regimes, adversarial inputs, consistency probes, and capability probes. Foreknowledge bias detection (rules-based, simple). Calibration scoring on resolved scenarios. Decision journal and principles document in place. Architecture A runs live in paper.

**Phase 3 — Architecture B (AIA-style) in parallel; eval expansion.**
Goal: Architecture B — Planner → Supervisor → Specialists with supervisor reconciliation — built and running against the same eval scenarios as Architecture A. Specialists are deliberately narrow (e.g., MacroAnalyst, DataFinder, CausalReasoner, MarketAnalyst, Critic). Eval harness expanded to ~20 scenarios. Calibration and trajectory-level scoring as first-class metrics. Inter-judge reliability on at least one LLM-judge dimension.

**Phase 4 — Live capital; memory iteration; instrumentation buildout.**
Goal: small live capital ($1–2k) deployed alongside paper for the chosen architecture. Daily reconciliation between backtest, paper, and live. Memory system iterated based on observed patterns. Post-hoc analysis tools (trace viewer, drift detector, override tracker) built.

**Phase 5 — Deep-dive on harness vs. weights; writeup; optional stretches.**
Goal: a sharp, evidence-backed opinion on the harness-vs-weights frontier, with concrete observations from running both architectures. Reflection document drafted and refined. Demo path prepared.

Phase 5 stretches, in order of priority:

1. Build a parallel **LangGraph version of Architecture B**. Compare the experience. One day.
2. Run a small **adversarial robustness battery** — plant plausible-but-wrong context across many scenarios, score robustness degradation. One to two days.
3. **Small post-training experiment** — collect ~100 of the agent's reasoning traces, SFT a small open-source model on them, see whether it can mimic the supervisor's reconciliation behavior. One to two days. Plays directly to the author's resume and Linsky's hiring language.

## 8. Collaboration model

**Three parties, distinct roles:**

- **Aniket** sets direction, makes architectural decisions in Plan Mode discussions, writes debriefs, owns the project's intent and POV.
- **Claude (this conversation)** produces the project charter, phase prompts, and post-phase review of debriefs. Operates at the level of intent, constraints, and design. Does not write production code for the project.
- **Claude Code** is the implementation collaborator. Receives a phase prompt, produces a plan in Plan Mode, debates the plan with Aniket, then executes the approved plan. Operates at the level of components, implementation, and execution.

**Specification philosophy (Option B with carve-outs):**

Phase prompts specify _goals and constraints_. Claude Code decides _components, APIs, and implementation_ and discusses them with Aniket in Plan Mode.

Carve-outs where the prompt specifies more tightly:

- The risk gateway's invariants (Section 6).
- The instrumentation trace schema (defined in Phase 1 and frozen).
- The bitemporal data model.
- The eval harness's required dimensions (foreknowledge, calibration, trajectory, inter-judge).
- Anything where correctness is more important than design taste.

Everything else is open for discussion.

**Cadence:**

- **Phase start:** Aniket gives Claude Code the phase prompt. Claude Code enters Plan Mode and produces a plan. Aniket debates, pushes back, approves.
- **During phase:** Claude Code executes the approved plan. Aniket reviews work, makes course corrections, observes the running system.
- **Phase end:** Aniket writes the debrief (template below) and brings it to Claude (this conversation). Claude produces the next phase's prompt informed by the debrief.

## 9. Phase debrief template

The debrief is the feedback loop that prevents the next phase's plan from being fiction. It has six sections:

1. **The plan Claude Code proposed** — paste the Plan Mode output, or a faithful summary if very long.
2. **Where I pushed back and why** — the design discussion, especially decisions that surprised you in either direction.
3. **What got built** — factual; what changed from plan to reality, and why.
4. **What I observed** — patterns, surprises, system behavior. Concrete, not general. Anchored where possible to specific traces.
5. **What's unresolved** — questions for next phase or for Claude (this conversation).
6. **Raw traces** — 1–2 representative agent traces, verbatim, so Claude can see what the system actually does rather than what you think it does.

The debrief is also a real interview artifact. Treat it accordingly.

## 10. Known traps

These are failure modes anticipated from the start. Naming them upfront makes them resistible.

**Substrate creep.** The pull to keep improving data quality, backtest fidelity, risk gateway sophistication. Resist after Phase 1.

**Architecture proliferation.** Wanting to build a third architecture. Two done well is the cap.

**Live P&L gravity.** Once real money is in, the urge to watch and tweak. P&L is the risk gateway's problem.

**Finance LARPing.** Spending preparation on quant fluency you don't need. The role wants AI systems depth.

**Generic agent observations.** "I noticed sycophancy" is worthless. "I noticed sycophancy in _this scenario_ and _this intervention_ helped" is the entire point.

**Framework rabbit holes.** Even with the Phase 5 LangGraph stretch, do not let it eat a week. One day, with a written comparison artifact.

**Causal cargo-culting.** Sprinkling "causal" through the project without engaging with what it actually changes. Either the DAG constrains the agent's reasoning in a concrete, observable way, or it isn't there.

**Cloning AIA without critiquing.** Architecture B is faithful, then critiqued. If the debrief for Phase 3 contains no "this felt forced because…" observations, the phase didn't accomplish its purpose.

## 11. Success criteria

The project succeeds if, walking into an AIA interview, the author can:

- Sketch the architecture on a whiteboard in 60 seconds, using AIA's vocabulary (Perceive/Hypothesize/Investigate/Synthesize/Learn, bitemporal, diagnosable, planner/supervisor/specialists).
- Walk through the eval harness and explain its scoring dimensions, with at least one (foreknowledge, calibration, or trajectory-level) directly mirroring AIA Forecaster concerns.
- Tell three concrete, specific stories about things the system did that surprised them.
- Defend an opinion on the harness-vs-weights question, with evidence from running both architectures.
- Discuss causal reasoning at conversation depth, including a firsthand account of why generic ReAct/RAG breaks in macro settings.
- Articulate what they would change about AIA's apparent design, with reasoning grounded in having built something structurally similar.

If those six are true, returns are irrelevant. If those six are not true, profitable returns won't save the interview.
