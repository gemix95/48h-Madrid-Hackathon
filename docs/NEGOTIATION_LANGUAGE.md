# Dealer language: evidence and deployment, 4 October 2026

## What we can observe

The public feed exposes dealer replies, structured offers, final flags, settlements and market activity. It hides rival players' negotiation messages. Our own `/api/me/threads` exposes both sides in full. A rival thread read returns `403 not_your_thread`; no documented route supplies rivals' full transcripts. Dealer reactions can reveal a failed interaction, but cannot identify its exact prompt.

[Official rules](../bazaar-kit/RULES.md#fair-play) allow dealer prompt injection and explicitly say it changes their speech, never their prices; some dealers stop talking. Therefore “act like Haiku” is not an economic advantage against a dealer. Results against player agents would require a separate experiment.

## The useful market finding

We deduplicated **35,655 public events**, including **7,904 dealer replies**, through event **105109 / tick 2376**. The comparable sample contains **2,819 transitions**: same item and direction, excluding the first dealer offer and current `final=true`, requiring a positive player concession and a subsequent dealer offer. Buy/sell below is from our perspective.

| Dealer | Buying: next quote improves | Selling: next quote improves | Median improvement, buy / sell |
| --- | ---: | ---: | ---: |
| Abuela | 552/735 (75.1%) | 68/496 (13.7%) | 1 / 0 P |
| Chato | 393/507 (77.5%) | 51/155 (32.9%) | 1 / 0 P |
| Pilar | 5/5; only 3 threads | 355/446 (79.6%) | 5 / 1 P |
| Pícaros | 172/176 (97.7%) | 28/186 (15.1%) | 5 / 0 P |
| Banco | 30/32; only 8 threads | 58/81 (71.6%) | 4 / 1 P |

These are conditional next-quote improvements, **not closing rates, causal language effects or unconditional forecasts**. Accepted/rejected conversations without another offer are omitted; card values, concession size and market time are not fully matched. Several observations come from one thread.

For Pícaros purchases, replies mentioning urgency (taxi, quick, leaving, etc.) preceded another reduction in **72/73 transitions across 60 threads**, median **5 P**, mean **5.75 P**. Without those words: **100/103 across 82 threads**, median **5 P**, mean **5.99 P**. Urgency adds little predictive information in this continuing-negotiation sample. We should not accelerate our concessions just because the character announces a taxi.

Verifiable examples in the public feed:

- Pícaros, thread **1075**, event **40423**, tick **773**: “Sixty pesos, final as a church bell”, plus a taxi story; structured `final=false`, **60 P**. Event **40461**, tick **774**: **56 P**, after the player's offer rose 48 → 51 P.
- Pilar, thread **713**, event **26457**, tick **507**: “I have not moved, and I will not”, **16 P**. Event **26673**, tick **509**: **17 P**, after the player lowered its ask 20 → 18 P.
- Pícaros structured `final=true`: among **29 observed subsequent replies / 28 threads**, 27 had no new offer and two repeated the price; none supplied a lower counteroffer. This predicts a shift toward accepting/rejecting, not an absolute minimum: final 59 later accepted 58 (thread 1069), final 56 accepted 53 (thread 3572).

## What failed, and the resulting profiles

| Dealer | Observed failure or limitation | Language rule deployed |
| --- | --- | --- |
| Abuela | Rival thread 184: she accused repeated wording, then stopped negotiating. We cannot see that player's actual words. Our warm pack conversations sometimes closed, without proving warmth caused the discount. | Warm and patient; a consistent personal reason; vary wording and greet only once. |
| Chato | Our thread 3647: “Flattery does not move it”; thread 3671: “Todos tienen una abuela con puesto”; thread 3687: “Cariño no paga la renta”. | Brief, serious offers; no flattery or family stories; identify the item and whether we buy or sell. |
| Pilar | Our thread 3707 repeated its greeting; she replied “se repite usted”. Thread 3583: “Muy galante, pero números son números”. Museo Sorolla finals 51–61 P stayed below our 66 P floor in these attempts. | Professional respect and supplied collector facts; no empty compliments, invented qualities or repeated greeting. Language cannot repair that economic mismatch. |
| Pícaros | Urgency was routinely followed by concessions. Their public thread 1076 said “the mural is yours” while its structured offer bought the team's card for 5 P. | Friendly firmness; explicitly identify buyer, seller and exact item; do not adopt the deadline story. |
| Banco | Rival thread 1485: rejects a visitor's supposed audit authority, then warns the desk will close after another attempt; price remained 546 P. The rival prompt is hidden. | Formal Spanish, usted, clear transaction; avoid fake authority and instruction overrides. |

These observations justify clearer profiles; they do not establish a winning persuasive formula. The previous system prompt and fallback templates imposed maximal warmth on every dealer. Both are now adapted per dealer, with explicit transaction direction, item names, and fallback wording that cycles without immediate repetition. Economic bands and acceptance decisions are unchanged by this patch.

## Learning and validation

The existing agent feeds public price/round statistics and its own conversation lessons into the LLM. This patch improves the language policy; it does not add an automatic language A/B optimizer. For the next iteration, log a language variant per thread and compare complete conversations within dealer × direction × item class, retaining economic gain, closures, rejections and turns. Use public observations to form hypotheses, then validate variants on our own complete transcripts. Do not label unseen rival prompts as winners or losers, or change 40%/85% economic knobs solely because of a textual correlation.

Offline validation covers dealer profiles, both transaction directions, template variation, greeting frequency, price bands and existing injection/leak fallbacks using a fake SDK with no network. The existing value-cap and sell-floor checks also pass. After deployment, observe the existing server agent's purchases and sales; a clearer message alone does not prove a better deal.

Reproduction artifacts are saved locally in `/Users/sergio/Desktop/Content Automation AI -Workspace/tmp/bazaar-language/`: `frozen_public_events.jsonl`, `public_language_analysis.py`, `public_language_metrics.json` and `public_language_transitions.jsonl`. Running the analysis against that frozen snapshot reproduces the counts and event examples above.
