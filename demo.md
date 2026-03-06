# Demo — Vera VoC Agent
*Autonomous Voice of Customer Intelligence via OpenClaw + Groq LLaMA 3.3 70B*

## Setup
- Agent Framework: OpenClaw with Groq LLaMA 3.3 70B
- Products: Master Buds 1 vs Master Buds Max (Flipkart)
- Database: SQLite with 284 reviews total
- Interface: Telegram / CLI chat

## Query 1: Head-to-Head Comparison
**User:** "What does Master Buds Max do better than Master Buds 1 on ANC and Comfort?"
**Vera:** Based on 139 reviews of Master Buds Max and 145 reviews of Master Buds 1, Master Buds Max does better than Master Buds 1 on ANC, with 7 reviews mentioning excellent ANC and 2 reviews criticizing the comfort. For Master Buds 1, ANC is mediocre and there are mixed reviews on the comfort, with 4 reviews praising the comfort and 2 reviews criticizing it. However, it's essential to note that individual preferences and experiences may vary.
*Tools called: get-statistics, query-database*

## Query 2: Complaint Analysis
**User:** "What are the top 3 complaints about battery life on Master Buds 1?"
**Vera:** Based on 145 reviews of Master Buds 1, the top 3 complaints about battery life are:
1. Battery drains fast (cited in 2 reviews, e.g., "Battery life is disappointing. They claim 8 hours but I barely get 4.5 hours on moderate volume.")
2. Battery issues after update (cited in 1 review, e.g., "After the firmware update, battery life dropped from 6 hours to about 3. Something is seriously wrong with the battery management.")
3. Battery life does not justify the premium pricing (cited in 1 review, e.g., "At this price point there are better alternatives. The build quality and battery life don't justify the premium pricing.")
*Tools called: query-database (sentiment=Negative, theme=Battery Life)*

## Query 3: Marketing Action Items
**User:** "Give the Marketing team 3 specific action items based on all reviews."
**Vera:** Based on all reviews of Master Buds 1 and Master Buds Max, here are three specific action items for the Marketing team:

1. **Highlight the sound quality**: 5 reviews out of 284 praise the sound quality of Master Buds 1 and Master Buds Max, citing deep bass, clear mids, and crisp highs. Marketing campaigns can emphasize the excellent sound quality to appeal to audiophiles.
2. **Address battery life concerns**: 7 reviews express disappointment with the battery life of Master Buds 1, with some users experiencing shorter battery life after updates. Marketing materials can acknowledge these concerns and highlight any improvements made to battery management.
3. **Promote comfort and fit**: 4 reviews mention the comfort and fit of Master Buds 1 and Master Buds Max, with some users finding them comfortable for long sessions and others experiencing ear fatigue. Marketing can focus on the ergonomic design and lightweight build of the earbuds to appeal to users who value comfort and fit.

These action items can help the Marketing team tailor their messaging to address common customer concerns and highlight the key strengths of Master Buds 1 and Master Buds Max.
*Tools called: get-statistics, generate-report*

## Weekly Automation
OpenClaw heartbeat runs every Monday 9AM UTC.
Trigger: openclaw-config.json heartbeat → sends pipeline message to Vera → Vera calls skills autonomously.
GitHub Actions backup: .github/workflows/weekly_voc.yml

## Loom Recording
[Record a 2-3 minute Loom of: starting the agent → asking query 1 → asking query 3 → showing the generated report file]
Loom link: [ADD AFTER RECORDING]
