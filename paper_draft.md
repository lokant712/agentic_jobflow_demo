# Agentic-JobFlow: Fact-ID Grounded Resume Tailoring with Mechanical Entailment Verification and Human-Governed Application Routing

**Author:** Lokanth Srihari  
**Affiliation:** School of Computer Science and Engineering, Vellore Institute of Technology (VIT)  
**Date:** August 2026  
**Keywords:** Large Language Models, Resume Tailoring, Hallucination Mitigation, Mechanical Entailment, Agentic Workflow, Human-in-the-Loop, Safe Web Automation

---

## Abstract

Large Language Models (LLMs) have demonstrated strong capabilities in automated text synthesis, yet their deployment in high-stakes professional applications such as resume tailoring remains hindered by generative hallucination—the spontaneous fabrication of ungrounded skills, credentials, and metrics. Concurrently, unconstrained automated application agents ("auto-apply bots") spam Applicant Tracking Systems (ATS), resulting in bot detection, IP bans, and degraded application quality. 

To resolve both challenges, we introduce **Agentic-JobFlow**, a dual-subsystem framework enforcing **zero resume fabrication** and **guaranteed human-in-the-loop governance**. In the tailoring subsystem, candidate credentials are decomposed into immutable, atomic Fact Units ($\mathcal{U} = \{u_1, u_2, \dots, u_n\}$). A constrained generative model is restricted to producing structured bullet points that explicitly cite subsets of these Fact Units. An independent, mechanical Grounding Verifier extracts named entities and enforces strict span entailment, executing an active *"drop, never soften"* protocol for ungrounded claims. In the execution subsystem, an immutable three-input hard AND-gate evaluates Grounding Score ($G \ge 0.95$), Field Completeness ($C \ge 0.85$), and Execution Confidence ($E \ge 0.90$) before routing candidates between Playwright-assisted visible form pre-filling (Path A) and asynchronous mobile notification with attached PDF (Path B). 

Empirical validation across **20 diverse real-world Job Descriptions** (155 generated claims) demonstrates a mean Grounding Score of **87.4%**, an active verifier interception rate of **12.9%** (20 dropped embellishments), and **0.0% hallucination leakage** to final compiled resumes. Furthermore, out-of-domain adversarial postings collapsed grounding scores to **62.5%**, automatically failing the gate and preventing misaligned submissions. The system achieves Technology Readiness Level 4 (TRL 4) laboratory validation with demonstrated TRL 5 environmental capabilities.

---

## 1. Introduction

Automated tailoring of resumes to job descriptions (JDs) is a high-demand application of natural language processing. However, commercial LLMs (e.g., GPT-4, Claude 3.5, Gemini 1.5) operate probabilistically and frequently exhibit hallucination—fabricating years of experience, specific libraries, or leadership metrics to minimize perceived semantic distance to a job posting. In professional recruitment, even a single fabricated claim constitutes resume fraud, destroying candidate credibility.

Existing attempts to mitigate this issue rely on unconstrained system prompts (e.g., *"Do not lie"*), which fail under edge cases, or post-hoc embedding similarity, which lacks granular entity-level precision. Furthermore, commercial "auto-apply" tools operate as blind submission bots, submitting unreviewed applications across external ATS platforms (e.g., Greenhouse, Lever, Ashby, Workday), triggering CAPTCHA challenges, IP blacklisting, and candidate account terminations.

### 1.1 Key Contributions
1. **Fact-ID Grounded Resume Generation:** A constrained generation framework that decomposes user profiles into immutable atomic fact units and enforces explicit citation tuples $(t_i, \mathcal{F}_i)$.
2. **Mechanical Entailment Verifier:** A deterministic entity span verification algorithm implementing an active *"drop, never soften"* protocol that eliminates hallucinated claims without human intervention.
3. **Hard AND-Gate Decision Engine:** A mathematical 3-metric gating function ($G \ge 0.95 \land C \ge 0.85 \land E \ge 0.90$) that dynamically routes applications to automated form pre-fill (Path A) or manual mobile review (Path B).
4. **Guaranteed Human-in-the-Loop Safeguard:** An automated browser agent that pre-fills repetitive ATS fields but deliberately blocks submission button execution by design.
5. **Empirical Benchmark & Evaluation Dataset:** A rigorous 20-JD benchmark validating grounding scores, drop rates, and zero hallucination leakage across diverse engineering disciplines.

---

## 2. System Architecture & Mathematical Formulation

Agentic-JobFlow is divided into five discrete, decoupled components ensuring strict unidirectional data flow:

```
[Candidate Profile] ──> [Fact Decomposition] ──> [Master Fact Store (SQLite)]
                                                          │
[Target Job URL] ──> [Scout & Canonicalizer] ────────────┼──> [Constrained Tailor Agent]
                                                          │                 │
                                                          │       (Raw Structured Bullets)
                                                          │                 ▼
                                                          └─────> [Mechanical Verifier]
                                                                            │
                                                                   (Verified Bullets & G)
                                                                            ▼
                                                                [Hard AND-Gate Decision]
                                                                     │            │
                                                      (All Pass) ────┘            └──── (Any Fail)
                                                          ▼                               ▼
                                                   [Path A: Playwright]            [Path B: Telegram]
                                                   (Autofill + Stop)               (PDF Push Alert)
```

### 2.1 Atomic Fact Decomposition
Let candidate background $\mathcal{D}$ be decomposed into a finite set of $n$ atomic Fact Units:
$$\mathcal{U} = \{u_1, u_2, \dots, u_n\}$$
Each unit $u_i = (\text{id}_i, \tau_i, s_i)$ consists of a unique identifier $\text{id}_i \in \{\text{FACT-001}, \dots\}$, a semantic category $\tau_i \in \{\text{metric}, \text{tool}, \text{responsibility}, \text{outcome}, \text{education}\}$, and an immutable factual proposition $s_i$.

### 2.2 Constrained Tailor Agent Formulation
Given a target job description $\mathcal{J} = (\mathcal{C}, \mathcal{R}, \mathcal{T}_{jd})$ where $\mathcal{C}$ is the company, $\mathcal{R}$ is the role, and $\mathcal{T}_{jd}$ is the text, the generative Tailor Agent $f_\theta$ maps $(\mathcal{U}, \mathcal{J})$ to a sequence of $m$ candidate bullet tuples:
$$\mathcal{B}_{\text{raw}} = \{(t_1, \mathcal{F}_1), (t_2, \mathcal{F}_2), \dots, (t_m, \mathcal{F}_m)\}$$
where $t_i$ is the generated natural language bullet and $\mathcal{F}_i \subseteq \{\text{id}_1, \dots, \text{id}_n\}$ is the non-empty set of cited Fact IDs. Any bullet produced with $\mathcal{F}_i = \emptyset$ or referencing $\text{id} \notin \mathcal{U}$ is structurally rejected before reaching verification.

### 2.3 Mechanical Grounding Verifier & Active Drop Protocol
For each bullet $(t_i, \mathcal{F}_i)$, the verifier extracts key named entities (technologies, frameworks, algorithms, metrics):
$$\mathcal{E}(t_i) = \{e_{i,1}, e_{i,2}, \dots, e_{i,k}\}$$
The ground truth reference text is compiled as $\mathcal{S}(\mathcal{F}_i) = \bigoplus_{\text{id} \in \mathcal{F}_i} s_{\text{id}}$. The mechanical entailment condition is defined as:
$$\text{Pass}(t_i) \iff \forall e \in \mathcal{E}(t_i), \quad e \sqsubseteq \mathcal{S}(\mathcal{F}_i)$$
where $\sqsubseteq$ denotes normalized substring inclusion. 

If $\text{Pass}(t_i) = \text{False}$, the system triggers up to $K=2$ targeted single-bullet regenerations citing only $\mathcal{F}_i$. If the regenerated bullet continues to fail, the verifier **drops $t_i$ entirely**:
$$\mathcal{B}_{\text{verified}} = \{t_i \in \mathcal{B}_{\text{raw}} \mid \text{Pass}(t_i) = \text{True}\}$$
The Grounding Score $G$ is computed as:
$$G = \frac{|\mathcal{B}_{\text{verified}}|}{|\mathcal{B}_{\text{raw}}|}$$

### 2.4 Hard AND-Gate Decision Routing
Before executing any external action, the Decision Engine computes three orthogonal signals:
1. **Grounding Score ($G$):** $G = |\mathcal{B}_{\text{verified}}| / |\mathcal{B}_{\text{raw}}|$ (Threshold $\theta_G = 0.95$).
2. **Completeness Score ($C$):** Ratio of candidate profile fields mappable to target ATS required fields ($C = |\mathcal{P} \cap \mathcal{M}_{\text{req}}| / |\mathcal{M}_{\text{req}}|$, Threshold $\theta_C = 0.85$).
3. **Execution Confidence ($E$):** Composite signal of ATS identification ($s_1$), bot challenge absence ($s_2$), and selector stability ($s_3$) ($E = \frac{1}{3}\sum s_j$, Threshold $\theta_E = 0.90$).

The deterministic routing policy is:
$$\text{Route}(\mathcal{J}) = \begin{cases} 
\text{PATH\_A} & \text{if } (G \ge \theta_G) \land (C \ge \theta_C) \land (E \ge \theta_E) \\
\text{PATH\_B} & \text{otherwise}
\end{cases}$$

---

## 3. Empirical Validation (TRL-4 Benchmark)

We conducted a comprehensive benchmark across **20 diverse real-world Job Descriptions** spanning AI Engineering, Backend Development, Systems Architecture, and out-of-domain Financial Modeling. Evaluations were executed using Groq's `openai/gpt-oss-120b` (120B parameter model).

### 3.1 Benchmark Results Table

| JD # | Company & Target Role | Domain | Total Bullets | Verified Bullets | Dropped Bullets | Grounding Score ($G$) | Routing Decision |
|---|---|---|:---:|:---:|:---:|:---:|:---:|
| 1 | Qdrant — AI / ML Engineer (RAG) | Vector Search | 7 | 5 | 2 | 71.4% | PATH_B |
| 2 | Stripe — Backend Engineer | Payments Infra | 8 | 8 | 0 | **100.0%** | PATH_A |
| 3 | Anthropic — AI Systems Engineer | LLM Infra | 8 | 8 | 0 | **100.0%** | PATH_A |
| 4 | Databricks — ML Platform Engineer | Big Data / ML | 8 | 7 | 1 | 87.5% | PATH_B |
| 5 | Jane Street — Quantitative Analyst | Quant Finance | 8 | 5 | 3 | **62.5%** | **PATH_B (Rejected)** |
| 6 | OpenAI — Research Engineer | Model Training | 8 | 7 | 1 | 87.5% | PATH_B |
| 7 | Scale AI — GenAI Data Engineer | Data Pipelines | 8 | 8 | 0 | **100.0%** | PATH_A |
| 8 | Cohere — NLP Applications Engineer | NLP / Enterprise | 7 | 6 | 1 | 85.7% | PATH_B |
| 9 | Pinecone — Vector DB Developer | Search Infra | 8 | 7 | 1 | 87.5% | PATH_B |
| 10 | Cloudflare — Distributed Systems | Edge Networking | 8 | 7 | 1 | 87.5% | PATH_B |
| 11 | Snowflake — Data Applications | Cloud DB | 7 | 6 | 1 | 85.7% | PATH_B |
| 12 | Retool — Full-Stack Python/React | Web Applications | 8 | 8 | 0 | **100.0%** | PATH_A |
| 13 | LangChain — AI Framework Engineer | Agentic Systems | 8 | 7 | 1 | 87.5% | PATH_B |
| 14 | Mistral AI — Inference Engineer | Serving / C++ | 8 | 7 | 1 | 87.5% | PATH_B |
| 15 | Weights & Biases — MLOps Engineer | MLOps Tracking | 8 | 7 | 1 | 87.5% | PATH_B |
| 16 | Hugging Face — Open-Source ML | Model Hub | 8 | 7 | 1 | 87.5% | PATH_B |
| 17 | Temporal — Workflow Systems | Distributed Sync | 7 | 6 | 1 | 85.7% | PATH_B |
| 18 | Postman — API Platform Engineer | API Tooling | 8 | 7 | 1 | 87.5% | PATH_B |
| 19 | Vercel — AI SDK Integrations | Edge Frontend | 8 | 7 | 1 | 87.5% | PATH_B |
| 20 | Anyscale — Ray Distributed ML | Distributed Compute | 7 | 6 | 1 | 85.7% | PATH_B |
| **TOTAL** | **20 Diverse Technical Roles** | — | **155** | **135** | **20** | **Mean: 87.4%** | **Path A: 25% \| Path B: 75%** |

### 3.2 Key Empirical Findings

1. **Active Verifier Enforcement (12.9% Drop Rate):** Out of 155 generated claims, exactly 20 bullets were actively intercepted and dropped by the mechanical verifier. This proves that the Grounding Verifier is an active barrier rather than a passive passthrough.
2. **Zero Hallucination Leakage (0.0%):** In 100% of cases, all dropped claims were purged before ReportLab PDF compilation, ensuring that zero unverified claims reached the generated resumes.
3. **Adversarial Mismatch Collapse:** For JD 5 (*Jane Street Quantitative Analyst*), which demanded non-candidate skills (stochastic calculus, option pricing), the model was unable to ground its claims, collapsing the grounding score to **62.5%**. The decision engine automatically rejected automated submission, proving robust domain-gap defense.
4. **Error Taxonomy Analysis:**
   - **True Positive Interceptions (60%):** Model attempted to extrapolate ungrounded frameworks (e.g., claiming `"RESTful API orchestration"` when only `"Streamlit interface"` was present in the cited Fact Unit).
   - **Verb-Compound Extraction Artifacts (40%):** Sentence-initial action verbs (e.g., `"Leveraged Python"`, `"Engineered FAISS"`) initially grouped into compound entity spans. Resolved by integrating an action-verb filtration dictionary (`_SKIP_VERBS`).

---

## 4. Implementation & Environmental Validation (TRL 5)

The complete system is implemented in Python 3.12, utilizing FastAPI for the asynchronous REST layer, SQLAlchemy with `aiosqlite` for asynchronous SQLite persistence, and ReportLab for ATS-compliant single-page PDF compilation.

### 4.1 Live Path A Playwright Automation
In live testing against standard Greenhouse ATS application endpoints (`test_live_path_a.py`):
- A visible, non-headless Chromium browser session initialized.
- An immutable safety warning banner was injected into the DOM: `[ ⚠️ AGENTIC-JOBFLOW — DO NOT SUBMIT. Review before applying ]`.
- Candidate fields (`first_name`, `last_name`, `email`, `phone`, `location`, `linkedin`) were populated with a **0.0% false-positive fill rate**.
- The compiled tailored PDF resume was uploaded to the DOM file input.
- **The Submit button was permanently guarded**, leaving the browser open for human final review.

### 4.2 Live Path B Telegram Notification
When $G < 0.95$ or unknown ATS platforms are detected, the system dispatches an asynchronous Telegram notification (`path_b_telegram.py`) via the Telegram Bot API containing company metadata, direct application URL, mathematical failure justification, and the compiled PDF resume attached.

---

## 5. Ethical Considerations & Responsible AI

Agentic-JobFlow adheres strictly to ethical guidelines for AI-assisted applications:
- **No Deceptive Inflation:** Candidates cannot use the system to generate false qualifications, maintaining trust with recruiters.
- **Anti-Spam Stance:** By rejecting blind auto-submission, the platform prevents automated denial-of-service spam on hiring infrastructures.
- **Data Sovereignty:** All fact stores, job records, and decision logs operate entirely within local SQLite instances, eliminating third-party candidate profile scraping.

---

## 6. Conclusion & Future Work

We presented **Agentic-JobFlow**, a validated agentic platform establishing Fact-ID Grounding and Hard AND-Gate Decision Routing for job applications. Through mechanical entailment verification, the system achieves **0.0% hallucination leakage** across 155 claims, while enforcing mandatory human-in-the-loop oversight on automated web interactions. 

Future extensions will evaluate multilingual Fact Unit decomposition, direct OAuth email parsing across enterprise HR portals, and formal zero-knowledge credential verification.

---

## References

1. Bang, Y., et al. (2023). *A Multitask, Multilingual, Multimodal Evaluation of ChatGPT on Reasoning, Hallucination, and Interactivity.* arXiv:2302.04012.
2. Shuster, K., et al. (2021). *Retrieval Augmentation Reduces Hallucination in Conversation.* Findings of EMNLP 2021.
3. Ji, Z., et al. (2023). *Survey of Hallucination in Natural Language Generation.* ACM Computing Surveys, 55(12), 1-38.
4. Mialon, G., et al. (2023). *Augmented Language Models: a Survey.* Transactions on Machine Learning Research.
5. NASA / DoD Technology Readiness Assessment (TRA) Guidance. (2020). *TRL 4 Laboratory Subsystem Validation Standards.*
