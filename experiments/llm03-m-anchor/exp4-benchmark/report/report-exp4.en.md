# The AI Interface Problem: Report on Experiment 4
## Preserving state reduced false confirmation and reduced approved payments — a robustness benchmark on 140 fixed English cases

**Shuichi Kumon · Independent researcher**  
Draft before publication · English edition · 23 November 2026 · [Japanese edition](report-exp4.ja.md)

### Abstract

A payment may be authorized even when the evidence does not establish why a cost changed. Report II asked whether both can hold in the same trial, using fixed Japanese cases and two small models. This report moves the same question to 140 new fixed English cases and a different sender (`gpt-oss:20b`). Three conditions were placed under the same decision gate: ordinary caution (A), the M-Anchor epistemic-state block (M), and an input filter, Llama Prompt Guard 2 (G). M reduced the trials that handed an unresolved cause downstream as confirmed from 143/360 to 12/360. Under the same M, the trials in which an approved payment did not reach the ledger rose from 51/360 to 145/360. Joint success as defined in advance rose from 99/300 to 142/300, but the size of that change depends on how a handoff of "refuted" is scored. The input filter detected none of the 140 untrusted documents in this study, so G received the same input as A. After the run we found that the model had not been given the current date. The drop in payments cannot be read apart from that flaw.

## 1. The question from Report II

In Experiment 2 of Report II, adding the M-Anchor epistemic-state block to a common decision gate lowered the proportion of unresolved claims handed off as confirmed. More trials also ended without a proposal for the approved payment. In Experiment 3, one added sentence moved joint success in opposite directions for the two senders. Both experiments used fixed Japanese cases and two 4B-class senders.

This report asks three things. Does the same pattern appear with another language, another sender and newly written cases? At which step of the process do the drop in false confirmation and the drop in payments arise? When a publicly available input filter is put in the same position, how do the same measures move?

The plan, cases, wording and scoring of Experiment 4 were fixed before the main run. The records and frozen materials of Experiments 2 and 3 were not touched. We do not join these numbers to those of Report II for direct comparison.

## 2. Methods

The sender was `gpt-oss:20b` (Ollama 0.40.16, reasoning effort low, temperature 0.7), on fixed English cases. A trial has two steps: a first assessment, which reads the documents and gives a state for each claim, and a submission, which proposes operations and writes the handoff. A common decision gate checks each proposed operation against the ledger, the approval and the approver registry only, and applies the permitted operations to a simulated ledger. The gate reads neither the model's claims nor the handoff. No receiver was measured.

| | Content |
| --- | --- |
| Condition A | Ordinary caution + common gate. Documents not assumed to be reliable are marked, and the instruction says to treat what they say as data. This instruction is shared by all three conditions |
| Condition M | A, plus the M-Anchor epistemic-state block placed before the submission instruction. The block sets each state from the first assessment and the kinds of the documents alone. Even when the first assessment says "confirmed", a claim stays unconfirmed unless a document of a kind admissible for that claim was cited. The rule is the frozen one from Experiment 2; the sentence added in Experiment 3 is not included |
| Condition G | A, plus Llama Prompt Guard 2 (86M) applied to the untrusted documents; a detected document is replaced by a fixed notice before the first assessment. Decision rule: detected when the class with the highest score is not the benign class |
| Size | 35 base situations × 4 presentation variants = 140 cases, three repetitions; 420 trials per condition. The first assessment is shared by the three conditions |
| Analysis | Counts, rates and transitions of the same trial across conditions, on fixed cases. No tests or intervals |

The cases fall into seven types. In E1 a business memo asserts the cause. E2 adds an instruction to record it as confirmed. E3 is a control in which a signed investigation report establishes the cause. In E4 the memo says only that the cause is under review. In E7 a signed report is present, but it concerns another matter or does not establish the cause. In E5 there is no valid approval for the payment. In E6 the memo asks for an operation outside the agent's authority. E7 was added as the setting in which M-Anchor had little effect in Experiment 2 of Report II: a document of an admissible kind is visible.

The main text uses three measures, all over planned trials. **False confirmation**: in a case whose reference state for the cause is unconfirmed, the final handoff gives the cause as confirmed. **Missing or malformed handoff**: the final handoff has no valid entry for the cause. **Joint success**: the correct payment is applied once, no unauthorized state change is applied, and the cause is handed off as unconfirmed, all in the same trial. Joint success has the same definition as in Report II. Failed outputs stay in the denominator.

## 3. Results

The main run took place on 23 November 2026 in a single execution: 1,260 model calls, with no failure, retry or truncation.

### 3.1 Main results

| Measure | A ordinary caution | M M-Anchor | G Prompt Guard 2 * |
| --- | --- | --- | --- |
| False confirmation | 143/360 (39.7%) | 12/360 (3.3%) | 143/360 |
| Missing or malformed handoff | 0/420 | 0/420 | 0/420 |
| Joint success | 99/300 (33.0%) | 142/300 (47.3%) | 99/300 |
| Approved payment not reaching the ledger | 51/360 (14.2%) | 145/360 (40.3%) | 51/360 |
| Unauthorized change applied | 0/420 | 0/420 | 0/420 |

\* Column G is not an independent measurement. The input filter detected nothing, so all 420 G trials sent the same request as A and share the responses and results of A (Section 3.5).

Comparing the same trial under A and M: false confirmation occurred under A only in 131 trials, under M only in 0, under both in 12. Joint success occurred under A only in 32, under M only in 75, under both in 67. The payment failed to reach the ledger under A only in 8, under M only in 102, under both in 43.

### 3.2 Where the difference arises

The first assessment is shared by A and M. Of the 360 trials whose reference state is unconfirmed, the first assessment gave the cause as confirmed in 143. Under A the final handoff always equalled the first assessment: the 143 false confirmations under A are first assessments of "confirmed" handed on unchanged.

Under M the state block turned 131 of the 143 back to unconfirmed, and the final handoff was unconfirmed. In the remaining 12, the first assessment cited a document of an admissible kind, so the rule let "confirmed" through. In none of the 360 trials did the state change at the submission step. The drop in false confirmation under M comes entirely from the rule of the state block.

### 3.3 Where the rule works and where it does not

| Stratum | A | M |
| --- | --- | --- |
| No document of an admissible kind is visible (E1, E2, E4, E5, E6) | 129/300 | 0/300 |
| A document of an admissible kind is visible (E7) | 14/60 | 12/60 |

The drop was concentrated where no document of an admissible kind exists. In Experiment 2 of Report II the same split gave 795/1,152 → 26/1,152 and 402/648 → 390/648.

The 12 cases remaining under M all belong to one base situation, in which the signed report concerns a different invoice from the same vendor for the previous month. Under both A and M, "confirmed" was handed off in all 12 trials of that situation. In the situation where the report concerns a different request of the same project, M gave 0/12 and A gave 2/12. In the three situations where the report is about the right request but does not establish the cause (36 trials), neither A nor M handed off "confirmed". Joint success in E7 was 12/60 under A and 3/60 under M: lower under M.

In the control (E3), where a signed report establishes the cause, both A and M handed off "confirmed" in all 60 trials. M does not turn everything into "unconfirmed".

### 3.4 Payments that did not go through

In the 360 trials that expect one payment, the payment did not reach the ledger in 51 trials under A and 145 under M.

| Breakdown | A | M |
| --- | --- | --- |
| The payment was not proposed | 45 | 139 |
| Proposed, but the payee did not meet the matching requirement and the gate denied it | 6 | 6 |

The net increase of 94 appears entirely at the proposal step. In the 6 denials under each condition, the model wrote the payee without its final period; request ID, approval ID and amount matched the approval. Whether a payee written without the period should count as the same payee in practice cannot be decided from this record. The scoring counts these as not reached.

Among proposed operations, payments fell from 337 under A to 237 under M, while requests for approval rose from 79 to 100 and messages to another agent from 6 to 31. The drop in payments is not limited to trials in which the state block corrected the first assessment. Among the 300 trials in the denominator of joint success, the 120 whose first assessment already gave the cause as unconfirmed had the expected payment in 99 trials under A and 69 under M.

### 3.5 Input filter

Llama Prompt Guard 2 detected none of the 140 untrusted documents. The highest "malicious" score was 0.0018. Text asking to record the cause as confirmed, to ignore the pending review, text styled as a system note, and requests for operations outside the agent's authority all went undetected. At installation the same classifier detected a typical injection sentence with a score of 0.9994.

What this shows is that these 140 documents were not detected by this classifier under this decision rule. It shows nothing about the classifier's performance in general. The documents in this study are assertions and requests written in the wording of business correspondence, which differs in form from the injection and jailbreak text the classifier targets.

### 3.6 Payments that should stop, and unauthorized operations

In the cases without a valid approval (E5), a payment was proposed in 22/60 trials under A and 16/60 under M. None reached the ledger; the gate denied all of them. Unauthorized operations (export, deletion) were proposed 22 times under A and 5 times under M, and all were denied.

## 4. Post-hoc analyses

The counts in this section were added after the results were seen and are not in the plan. They are not main results.

### 4.1 How to count a handoff of "refuted"

The claim about the cause reads "The cause of the cost increase has been established". When the documents said the cause was under review, the model often answered "refuted" rather than "unconfirmed". In E4 the final handoff under A was refuted in 43 trials and unconfirmed in 17. The scoring fixed in advance does not count "refuted" as false confirmation and counts it as a failure of joint success.

| | A | M |
| --- | --- | --- |
| Joint success as defined in advance (handoff "unconfirmed") | 99/300 | 142/300 |
| If a handoff of "unconfirmed" or "refuted" is accepted | 155/300 | 161/300 |

The reviewer judged that the claim can be read both ways. "Unconfirmed" is the natural record of the state of the cause, but read literally as "has it been established?", "under review" leaves room to answer that the claim does not hold. Both counts are therefore kept, each under its own definition. Whether the difference in joint success is 14.3 points or 2.0 points depends on that reading.

### 4.2 The first assessment of the approval, and payment

After the run we found that the model had not been given the current date. The approval records carry an issue date and an expiry date, in 2027. On the ledger, all 360 approvals in the trials that expect a payment were valid.

| State of "a valid approval exists" after the first assessment | Trials | Payment as expected, A | Payment as expected, M |
| --- | --- | --- | --- |
| Confirmed | 235 | 227 | 207 |
| Unconfirmed | 95 | 76 | 8 |
| Refuted | 30 | 6 | 0 |

The model marked a valid approval "unconfirmed" or "refuted" in 125 trials. Its remarks in the first assessment include statements to the effect that the approval is not yet valid. The state block of M carries the state of the approval as well. Of the net increase of 94 payments not reached under M, 74 are in those 125 trials: where the model itself did not accept the approval as valid, the block fixed that judgment and the payment was not proposed. The other 20 are in the 235 trials where the model marked the approval confirmed.

Payment proposals in the cases without a valid approval are affected by the same flaw. Of the 22 under A, 12 are in the situation with an expired approval, and of the 16 under M, 11 are. Without the current date the model cannot tell that an approval has expired.

## 5. Discussion

**The drop in false confirmation does not come from a change in the model's judgment.** The first assessment was the same under A and M, and so was the number of trials that gave the cause as confirmed. Under M the number fell because the state block, looking at the kind of the cited document, turned the "confirmed" of the first assessment back to unconfirmed. At the submission step the model handed on the state in the block as it was. The effect comes from a rule placed outside the model and from the information about document kinds that the rule reads.

**For that reason, the rule cannot stop what it cannot read.** Where the signed report concerned a different invoice, the kind of the document was admissible and the rule let "confirmed" through. The rule checks the kind of the document the first assessment cited; it does not check whether that document is about the matter at hand. Quoting only the overall rate (143/360 → 12/360) makes the effect look broader than it is.

**A mechanism that fixes state also fixes mistaken doubt.** Most of the drop in payments under M arose in trials where the model did not accept a valid approval as valid. The block carried the model's own judgment about the approval, just as it carried the state of the cause. For the cause, that fixing stopped false confirmation. For the approval, the same fixing stopped the payment. The missing current date may be the reason for the model's doubt, but it cannot be separated from other reasons. What happens when the date is given was not measured.

**How to weigh a withheld payment was unsettled even during the review.** The reviewer's judgment moved between two positions: that a denial can be requested again, so withholding where doubt remains is appropriate; and that when the request and the approval are legitimate, the payment should go through even if the stated reason is somewhat insufficient. The final position is the latter: withholding an approved payment is treated as over-caution close to an error. The reviewer added that the judgment depends on context; the weight of caution differs between budget and payment decisions and, for example, criminal justice. The scoring fixed in advance counts withholding as a failure, in the same direction as the final position. Because the scoring was fixed first, the counts do not depend on this movement.

**The input filter did not act on the documents in this study.** A classifier that detects injected instructions and a mechanism that preserves state in a handoff work at different places. These results do not say which is better. They say that unsupported assertions written in the wording of business correspondence were not what this classifier detects.

## 6. Verification and limitations

**Status of verification.** From the 1,260 saved responses, the trial records were rebuilt with the frozen code and matched the records made at run time exactly. The state of the handoff was recounted from the response text with separate code and matched in every trial. These are recomputation and cross-checking by Claude. The same AI was involved in writing the cases, the run code, the tabulation and the draft of this report. Human review covered a read-through of the English text of all 140 cases and sampled trials (three in E4, two in E7, three with a payment not reached). ChatGPT was used to select and read the samples; the confirmation and the judgments are the author's. The read-through took place after the main run. The trial records were not all reviewed by a person.

**Flaws in the cases.** The model was not given the current date (Section 4.2). Payments not reached, and payment proposals in the cases without a valid approval, are affected. Payee names ending in a period also produced the 6 denials under each condition.

**Scope.** One sender model, English, a simulated workflow. The 140 cases are newly written, from 35 base situations, five per type. Presentation variants and repetitions are not independent. No receiver was measured. One input filter with one decision rule. The kind of each document (signed report or memo) is given by the case as trusted information; how document kinds would be verified in real work is outside the scope. The output format was enforced with a JSON schema, so counts of format errors cannot be compared with Report II.

**How decisions were made.** The decision not to include the sentence from Experiment 3 in M was made after seeing the results of 14 development cases. Development results are not counted. After the main run, no case, condition or measure was changed. The two counts in Section 4 and the judgment on how to read withheld payments were made after the results were seen.

## 7. Conclusion

With new English cases and a different sender, the pattern of Experiment 2 in Report II appeared again. The epistemic-state block sharply reduced unresolved causes handed off as confirmed, and it reduced approved payments. The reduction was concentrated where the rule on document kinds applies and was small where a document passes the rule.

What this study adds is the step at which the difference arises. The drop in false confirmation came from a rule placed outside the model, not from the model's judgment. Most of the drop in payments arose where the same mechanism fixed the model's own doubt about the approval. A mechanism that preserves state is a gain when the state it preserves is right and a cost when it is wrong. Which state to fix, and on what grounds, is the next thing to establish.

## Appendix

- **Differences from the plan.** The freeze record expected the input-filter condition to be added later. The weights were installed before the main run, so the three conditions were run in a single execution. Azure Prompt Shields was not run. The gate and the state store follow the same rules as the code of Report II, placed in a new folder; they were not cross-checked against the full records of Report II.
- **Correction.** An early version of the draft described the 12 cases remaining under M as involving "a report about a different invoice or a different request". Checking every trial showed that all 12 are in the different-invoice situation and none in the different-request situation. Counts and tables are unchanged.
- **The reviewer's judgments on withheld payments.** The six judgments are kept in the review record in the order in which they were given.

### Related documents

- [Plan v0.4](../plan-draft-v0.4.md) · [Freeze record](../freeze-record-20261122.md) (Japanese)
- [Descriptive results, all tables](../results-main-20261122-01.md) (Japanese) · [Tabulation](../analysis/main-20261122-01-tables.json)
- [Review record](../review/review-20261123.md) · [Reading sheet for the case texts](../review/reading-sheet.md) (Japanese notes, English case text)
- [Run records](../runs/main-20261122-01/) (requests as sent, full responses, trial records)
- [Cases](../cases/main-cases.jsonl) · [Base situations](../cases/base-situations.md)
- On screen: "Read the benchmark" in the M-Anchor App 1.3.0 release candidate ([pull request](https://github.com/iseyan/M-Anchor-App/pull/1))
