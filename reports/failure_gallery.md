# Failure gallery: how diffusion fails differently

Annotated examples for blog claims C6 and C11. Each one is a real job from a published run: the notes and picks are manual (`reports/failure_tags.csv`), the facts and excerpts are pulled from the raw logs by `dllm-eval report`. Long texts are cut in the middle. Counts per model for every tag are in [failure_modes.md](failure_modes.md).

## 1. More reasoning effort, no answer

`mercury-3:high/lcb-v6/abc388_d/0` in run `r2-uc1-main-20261021T034053Z` · tag `budget_out_hidden` (hit the output cap on hidden reasoning; no visible answer)

**What happened.** Mercury 3 at `high` spent the whole 32,768-token budget on hidden reasoning and returned nothing. The same problem is solved 5 of 5 times at `low` (6–12k tokens, 13–22 s) and at `instant`, but 0 of 10 times at `medium` and `high`. Raising the cap to 131,072 (R1b) rescued 1 of 6 such calls. Claude Haiku, Gemini, GPT-6 Luna `medium` and gpt-oss `medium` solve it 5 of 5.

**Facts.** finish: length · output tokens: 32567 · of which reasoning: 32567 · visible characters: 0 · time: 62.2 s · grade: no_code

**Prompt (excerpt).**

```text
Solve the following programming problem in Python 3. Your program must read from standard input and write the answer to standard output. Respond with the complete program in a single ```python code block.

On a certain planet, there are N aliens, all of whom a

[… 1,256 characters omitted …]
```

**Model output (excerpt).**

```text
(no visible text)
```

## 2. An ill-posed easy problem: the others guess, Mercury 3 keeps thinking

`mercury-3:medium/mbpp+/Mbpp/430/0` in run `r2-uc1-main-20261021T034053Z` · tag `budget_out_hidden` (hit the output cap on hidden reasoning; no visible answer)

**What happened.** The task says 'find the directrix of a parabola', but its one example (5, 3, 2 → −198) matches no textbook formula; the hidden reference uses an unusual one. Most AR arms write the standard formula and fail the tests (Claude Haiku `medium` reverse-engineers the example and passes 5 of 5). Mercury 3 reasons until the cap on all 10 samples at `medium` and `high`, and on 4 of 5 at `low`, apparently trying to reconcile the example with the math. Same score as a wrong guess, but ~32k tokens and close to a minute per call.

**Facts.** finish: length · output tokens: 32204 · of which reasoning: 32204 · visible characters: 0 · time: 53.8 s · grade: no_code

**Prompt (excerpt).**

```text
Write a Python function named `parabola_directrix` that solves the task below. Respond with the complete function and any imports it needs in a single ```python code block. Do not include tests or example usage.

Write a function to find the directrix of a parabola.
assert parabola_directrix(5,3,2)==-198
```

**Model output (excerpt).**

```text
(no visible text)
```

## 3. At `instant`, the runaway happens in plain sight

`mercury-3:instant/lcb-v6/arc194_a/0` in run `r2-uc1-main-20261021T034053Z` · tag `think_aloud_runaway` (hit the output cap while writing visible text)

**What happened.** With no hidden reasoning at `instant`, Mercury 3 reasons in the visible answer instead. On this hard LiveCodeBench problem it wrote ~138,000 characters of analysis ('Key observation… Instead, we can think in terms of…') and hit the cap before writing any code. 17 of 3,185 `instant` calls ended this way; no AR arm had a single one.

**Facts.** finish: length · output tokens: 31210 · of which reasoning: 0 · visible characters: 137633 · time: 51.8 s · grade: no_code

**Prompt (excerpt).**

```text
Solve the following programming problem in Python 3. Your program must read from standard input and write the answer to standard output. Respond with the complete program in a single ```python code block.

You are given an integer sequence of length N: (A_1, A

[… 1,458 characters omitted …]
```

**Model output (excerpt).**

```text
We need to maximize the final sum of a stack after processing elements sequentially, where at each step we either push the current element or pop the top (if non-empty).  

This is a classic dynamic programming problem. Let `dp[i]` be the maximum possible sum of the stack after processing the first `i` elements.  

Key observation:  
- If we push `A[i]`, the sum increases by `A[i]` and the stack size increases by 1.  
- If we pop, the sum decreases by the top element, but we don’t know what the top is without tracking stack contents.  

Instead, we can think in terms of the *size* of the stack

[… 136,733 characters omitted …]

inal stack is a subsequence, and it’s formed by taking some elements and discarding others. The discarding can happen at any time, but we can only discard the most recent. So if we want to discard an element that’s not the most recent, we must first discard all elements after it.  

Thus, the set of
```

## 4. A repetition loop inside a code literal

`mercury-3:instant/mbpp+/Mbpp/440/2` in run `r2-uc1-main-20261021T034053Z` · tag `think_aloud_runaway` (hit the output cap while writing visible text)

**What happened.** Asked to find adverbs in a sentence, Mercury 3 started a hard-coded list of adverbs and never closed it: ~110,000 characters of words, many repeated ('apparently' and 'seemingly' recur), until the cap. The code fence never closed, so the grader saw a syntax error. Degenerate repetition isn't specific to diffusion, but in R2 only Mercury 3 `instant` ever hit the cap while writing visible text.

**Facts.** finish: length · output tokens: 31712 · of which reasoning: 0 · visible characters: 110325 · time: 54.1 s · grade: error · tests passed: 0/106

**Prompt (excerpt).**

```text
Write a Python function named `find_adverb_position` that solves the task below. Respond with the complete function and any imports it needs in a single ```python code block. Do not include tests or example usage.

Write a function to find the first adverb and their positions in a given sentence.
assert find_adverb_position("clearly!! we can see the sky")==(0, 7, 'clearly')
```

**Model output (excerpt).**

```text
```python
import re

def find_adverb_position(sentence):
    adverbs = [
        "clearly", "quickly", "slowly", "happily", "sadly", "angrily",
        "quietly", "loudly", "carefully", "carelessly", "easily", "hard",
        "well", "badly", "fast", "late", "early", "soon", "now", "then",
        "here", "there", "always", "never", "often", "sometimes", "usually",
        "rarely", "seldom", "already", "yet", "still", "just", "also",
        "too", "very", "quite", "rather", "really", "almost", "nearly",
        "simply", "truly", "certainly", "surely", "probably", "possibly",
        "perhap

[… 109,425 characters omitted …]

ly", "evidently", "obviously", "clearly",
        "plainly", "manifestly", "patently", "apparently", "seemingly",
        "outwardly", "externally", "superficially", "nominally", "ostensibly",
        "apparently", "seemingly", "supposedly", "allegedly", "reportedly",
        "purportedly", "profess
```

## 5. In the agent loop: deliberating over data that isn't there

`mercury-3:instant/bird/1500/1` in run `r4-uc2-main-20261021T165313Z` · tag `budget_out` (an agent turn hit the output cap)

**What happened.** The question asks about September 2013, but the transactions table only covers August 2012. Mercury 3 `instant` noticed this correctly by turn 6, then kept reasoning in visible text in turn 8 ('Let me reconsider…') until the 32,768-token cap: ~146,000 characters, and 63 s for the task. 7 of 900 Mercury 3 `instant`/`low` tasks ended in a budget-out turn; no AR arm had any.

**Facts.** outcome: budget_out · turns: 8 · task time: 62.8 s · EX: False

**Prompt (excerpt).**

```text
Database: debit_card_specializing

Question: Please list the product description of the products consumed in September, 2013.

Hint: September 2013 refers to 201309; The first 4 strings of the Date values in the yearmonth table can represent year; The 5th and 6th string of the date can refer to month.
```

**Trace.**

  1. list_tables→ok
  2. describe_table→ok, describe_table→ok, describe_table→ok
  3. run_sql→ok · says "Let me check the transactions table for September 2013. The Date column is in format YYYY-MM-DD. Let me query for products consumed in September 2013."
  4. run_sql→ok, run_sql→ok · says "No results. Let me check the yearmonth table for September 2013, and also check the date formats more carefully."
  5. run_sql→ok, run_sql→ok · says "The yearmonth table has Date as '201309' format (YYYYMM). The transactions table has Date in 'YYYY-MM-DD' format. Let me check what dates exist in the transacti…"
  6. run_sql→ok, run_sql→ok · says "Interesting. The transactions_1k table only has dates from 2012-08. The yearmonth table has September 2013 data. Let me check if there are more transaction tabl…"
  7. run_sql→ok, run_sql→ok · says "The transactions_1k table only has data from 2012-08. But the yearmonth table has data for 201309. Let me check if there are more transaction tables or if the q…"
  8. no tool call · says "The transactions_1k table only has data from August 2012. The question asks about September 2013. Let me reconsider. Maybe the yearmonth table is the key here. …"

**Model output (excerpt).**

```text
The transactions_1k table only has data from August 2012. The question asks about September 2013. 

Let me reconsider. Maybe the yearmonth table is the key here. The yearmonth table has CustomerID, Date (YYYYMM), and Consumption. The question asks for product descriptions of products consumed in September 2013.

But how do I connect yearmonth to products? The yearmonth table doesn't have ProductID. 

Wait, maybe I need to look at this differently. Let me check if there are other transaction tabl

[… 146,077 characters omitted …]

nd I need to find products consumed in September 2013 by looking at the transactions for the customers who consumed in September 2013.

But the transactions_1k only has August 2012 data. So this doesn
```

## 6. The right answer, written as prose instead of a tool call

`gpt-oss-120b-cerebras:low/bird/1403/2` in run `r4-uc2-main-20261021T165313Z` · tag `turn_cap_prose` (8 turns without an answer, replying in prose instead of tools)

**What happened.** gpt-oss on Cerebras found its query, then replied with the answer as JSON text instead of calling `submit_answer`, twice, ignoring the nudge to use a tool, and ran out of turns. It is the fastest model per call, but behaviour like this (237 malformed tasks at `low`, 16.7% turn cap) is why its per-call lead mostly disappears per task.

**Facts.** outcome: turn_cap · turns: 8 · task time: 3.4 s · EX: False

**Prompt (excerpt).**

```text
Database: student_club

Question: Indicate the name of the closed event whose cost has exceeded the budget the most.

Hint: closed events refers to event_name where status = 'Closed'; exceed the budget the most refers to MIN(remaining) where remaining < 0
```

**Trace.**

  1. list_tables→ok
  2. describe_table→ok
  3. describe_table→ok
  4. run_sql→ok
  5. run_sql→ok
  6. run_sql→ok
  7. no tool call · says "{ "sql": "SELECT e.event_name FROM budget b JOIN event e ON b.link_to_event = e.event_id WHERE b.remaining < 0 AND e.status = 'Closed' ORDER BY b.remaining ASC …" · (nudged to use a tool)
  8. no tool call · says "{ "sql": "SELECT e.event_name FROM budget b JOIN event e ON b.link_to_event = e.event_id WHERE b.remaining < 0 AND e.status = 'Closed' ORDER BY b.remaining ASC …" · (nudged to use a tool)

**Model output (excerpt).**

```text
{
  "sql": "SELECT e.event_name FROM budget b JOIN event e ON b.link_to_event = e.event_id WHERE b.remaining < 0 AND e.status = 'Closed' ORDER BY b.remaining ASC LIMIT 1;"
}
```

## 7. Exploring one table at a time until the turns run out

`gemini-3.5-flash-lite:minimal/bird/240/1` in run `r4-uc2-main-20261021T165313Z` · tag `turn_cap_exploring` (8 turns without an answer, still exploring)

**What happened.** Gemini 3.5 Flash-Lite spends turns on schema exploration (several `describe_table` calls in separate turns, plus a failed query) and reaches the 8-turn cap before submitting. It hit the cap on 30.9% of tasks, the most of any arm, which is why its 0.63 s calls become a 4.4 s task.

**Facts.** outcome: turn_cap · turns: 8 · task time: 4.8 s · EX: False

**Prompt (excerpt).**

```text
Database: toxicology

Question: List all the elements of the toxicology of the molecule "TR004".

Hint: TR004 is the molecule id;
```

**Trace.**

  1. list_tables→ok
  2. describe_table→ok
  3. run_sql→sql_error
  4. describe_table→ok
  5. describe_table→ok
  6. describe_table→ok
  7. run_sql→ok
  8. run_sql→ok

## 8. Strict JSON with leaked tool-call markup

`mercury-3:instant/bird/169/0` in run `r4b-uc2-strict-20261021T170547Z` · tag `schema_violation` (final reply broke the strict JSON schema)

**What happened.** Under strict `response_format: json_schema`, Mercury 3 produced the right JSON object and then appended `</|inc_tool_call_ml|parameter>`, which looks like its internal tool-call template. Of its 6 violations, 4 end in this marker, 1 in a bare `</`, and 1 was empty. Every other arm had zero. A structured-output gap that appears only when tools are also offered.

**Facts.** outcome: invalid_answer · turns: 4 · task time: 1.3 s · EX: False

**Prompt (excerpt).**

```text
Database: financial

Question: What was the growth rate of the total amount of loans across all accounts for a male client between 1996 and 1997?

Hint: Growth rate = (sum of amount_1997 - sum of amount_1996) / (sum of amount_1996) * 100%; Male refers to gender = 'M'
```

**Trace.**

  1. list_tables→ok
  2. describe_table→ok, describe_table→ok, describe_table→ok, describe_table→ok
  3. run_sql→ok
  4. no tool call · says "{"sql": "SELECT (SUM(CASE WHEN strftime('%Y', l.date) = '1997' THEN l.amount ELSE 0 END) - SUM(CASE WHEN strftime('%Y', l.date) = '1996' THEN l.amount ELSE 0 EN…"

**Model output (excerpt).**

```text
{"sql": "SELECT (SUM(CASE WHEN strftime('%Y', l.date) = '1997' THEN l.amount ELSE 0 END) - SUM(CASE WHEN strftime('%Y', l.date) = '1996' THEN l.amount ELSE 0 END)) * 100.0 / SUM(CASE WHEN strftime('%Y', l.date) = '1996' THEN l.amount ELSE 0 END) AS growth_rate FROM loan l JOIN disp d ON l.account_id = d.account_id AND d.type = 'OWNER' JOIN client c ON d.client_id = c.client_id WHERE c.gender = 'M'"}</|inc_tool_call_ml|parameter>
```

## 9. The AR counterpart: confident, fast, and never looked

`claude-haiku-5-5:thinking-off/bird/959/0` in run `r4b-uc2-strict-20261021T170547Z` · tag `blind_wrong` (wrong answer submitted without exploring the database)

**What happened.** With a JSON schema set, Claude Haiku answered all 150 tasks in the first turn without calling a single tool, writing SQL from the question and hint alone. Here the guessed query returns the wrong rows. The schema was always valid; the exploration never happened: 53.3% EX versus 58.9% in tool mode.

**Facts.** outcome: submitted · turns: 1 · task time: 1.6 s · submitted SQL: SELECT lr.lap FROM lapTimes lr JOIN races r ON lr.raceId = r.raceId JOIN driverStandings ds ON ds.raceId = r.raceId AND ds.driverId = lr.driverId WHERE r.year = 2009 AND ds.position = 1 AND r.round = (SELECT MAX(round) FROM races WHERE year = 2009) ORDER BY lr.milliseconds ASC LIMIT 1 · EX: False

**Prompt (excerpt).**

```text
Database: formula_1

Question: What is the fastest lap number of the champion in 2009?

Hint: in 2009 refers to year = 2009; Only the time of the champion shows in the format of "hour: minutes: seconds.millionsecond"
```

**Trace.**

  1. no tool call · says "{"sql": "SELECT lr.lap FROM lapTimes lr JOIN races r ON lr.raceId = r.raceId JOIN driverStandings ds ON ds.raceId = r.raceId AND ds.driverId = lr.driverId WHERE…"

**Model output (excerpt).**

```text
{"sql": "SELECT lr.lap FROM lapTimes lr JOIN races r ON lr.raceId = r.raceId JOIN driverStandings ds ON ds.raceId = r.raceId AND ds.driverId = lr.driverId WHERE r.year = 2009 AND ds.position = 1 AND r.round = (SELECT MAX(round) FROM races WHERE year = 2009) ORDER BY lr.milliseconds ASC LIMIT 1"}
```

---

*Source: run r2-uc1-main-20261021T034053Z (commit 07dce9e, 2026-10-21); run r4-uc2-main-20261021T165313Z (commit 91c7204, 2026-10-21); run r4b-uc2-strict-20261021T170547Z (commit 3c4b5fc, 2026-10-21). Models: mercury-3, mercury-2.5, claude-haiku-5-5, gpt-6-luna, gemini-3.5-flash-lite, gpt-oss-120b. Standard API tiers (no paid priority); client one macOS arm64 host, US West; prices as listed 2026-10-20. As of 2026-10-21.*
