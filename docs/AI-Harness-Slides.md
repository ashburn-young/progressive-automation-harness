---
marp: true
title: The AI Harness
paginate: true
---

<!--
Slide-ready deck. Paste each slide into PowerPoint, or render directly with
Marp (marp-cli / VS Code "Marp for VS Code"). Speaker notes are the italic
"Say:" lines under each slide.
-->

# The AI Harness
### Turn manual work into AI skills that learn to run themselves

*Progressive Automation Harness*

*Say: We help teams automate the everyday tasks people still do by hand — safely, with humans in control.*

---

# The problem

- Big processes are already automated. The **"long tail"** isn't.
- Thousands of small, repetitive tasks — too minor for a project each.
- That work stays manual, locked in individuals' heads.

*Say: The trapped time in that long tail is the opportunity.*

---

# What is an AI Harness?

- The **safety scaffolding** around an AI agent.
- Lets AI do real work **while a human stays in control**.
- Captures every approval to **teach the AI over time**.
- Like a climbing harness: holds a task safely **until it can stand on its own**.

*Say: The harness is the part you eventually remove.*

---

# What is a Skill?

- The **reusable, auditable asset** the harness produces.
- Owned by the organization — not stuck in one person's head.
- **Matures:** Cold → Primed → Deterministic → Autonomous.
- The more it's approved, the more it runs itself — and the **cheaper** it gets.

*Say: The Skill — not a chat transcript — is the durable product.*

---

# How it works — 4 steps

1. **Describe** a repetitive task in plain language.
2. **Co-pilot** runs it; you **Approve / Reject / Edit** each step.
3. **Learn** — every approval trains the Skill.
4. **Reuse** — it takes over the steps you keep approving.

*Say: Supervision decreases as trust is earned, step by step.*

---

# Why it's different

- **Starts where the work already is** — low-risk, bottom-up adoption.
- **Humans in control by default** — risky actions always need a person.
- **Captures know-how** — continuity, onboarding, compliance.
- **Cost decays** — mature skills replay with **no AI call**.
- **Enterprise-ready** — secure, private, audited, on Azure.

*Say: The opposite of tools that pay full AI cost on every run, forever.*

---

# Trust & governance

- Every action **screened for safety** before it runs.
- **High-risk steps** (send, pay, submit) always require a human.
- **Full audit trail** of who approved what.
- Sign-in, private networking, no stored passwords.

*Say: The governance posture regulated industries require.*

---

# The Azure recipe

| Role | Azure service |
|------|---------------|
| Brain (reasoning) | Azure AI Foundry (models) |
| Orchestrator | Azure Container Apps |
| Tools | Azure Functions + MCP layer |
| Memory / Knowledge | Cosmos DB + AI Search |
| Guardrails | AI Content Safety |
| Identity & secrets | Entra ID + Managed Identity + Key Vault |
| Governance & network | API Management + VNet / Private Endpoints |
| Observability | Application Insights + Azure Monitor |

*Say: All managed services you already know how to govern.*

---

# It's a managed asset

- A **Skills Library** — the org's shelf of automations.
- **Quality grades A–D** across Safety, Completeness, Reliability, Maintainability, Cost.
- **Versioning + rollback.**
- **Lifecycle:** draft → validated → published → monitored → retired.

*Say: You only publish skills that measurably pass the bar.*

---

# The economics

- **Platform floor** — predictable base cost.
- **Usage** is highest **while learning**, then trends toward **near-zero**.
- Mature skills run for pennies (no AI call).

*Say: ROI improves the more it's used — not the reverse.*

---

# For buyers & sellers

**Buyers get:** a governed way to automate the long tail, with humans in control and cost that falls over time.

**Sellers lead with:** human-in-the-loop by default · the skill is an owned, auditable asset · cost decays · a real quality gate · runs on the customer's own Azure.

*Say: Who buys — operations, shared-services, finance, IT, and AI-transformation teams.*

---

# Honest scope

- A **working reference solution**, not a shrink-wrapped product.
- The learning loop, oversight, lifecycle, quality scoring, and Azure deployment are **real and demonstrable**.
- Connecting to a specific ERP / line-of-business system is a **straightforward first project** — not a rebuild.

*Say: That integration is the natural first engagement.*

---

# In one sentence

## Your approvals are the training data —
## so supervising the AI is what makes your supervision unnecessary,
## and the reusable Skill is the asset you can buy, sell, and govern.

*Say: Thank you — happy to demo it live.*
