# Usage, limits, and pricing

Use usage limits to bound a logical Run, usage records to understand its work, and pricing policies to estimate cost. These are different concerns: a request budget is enforcement, an observed cost is not an invoice, and a terminal result is not a durable billing record.

## Set a Run budget

```python
from a13n_harness import AgentSpec
from pydantic_ai.usage import UsageLimits

spec = AgentSpec(
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
    retries={"tools": 2, "output": 1},
)
```

A Run may replace the complete `UsageLimits` value; it does not merge individual fields. Tool/output retries do not configure network retries or interrupted-model recovery. See [usage limits and retries](agents-and-runs.md#usage-limits-and-retries) for defaults, children, and overrides.

## Usage

`stream.usage` is a live `RunUsageSummary`; `result.usage` is its terminal snapshot. Both derive from the same canonical `UsageRecord` union exposed by `stream.usage_records` and `result.usage_records`. They cover the local Run, including main LLM, review, compaction and auxiliary media models. Inline and asynchronous children own separate Harness Runs; their records are available to the Host for tree aggregation.

Requests and token/audio counters sum model records; cost includes model records and explicit provider receipts. Native model support for media creates no additional media request. Input tokens already include cache tokens. Cache hit rate is `sum(cache_read_tokens) / sum(input_tokens)`; zero input means unavailable.

For example, one agent request, one review and one separately billed search receipt can produce this summary (other counters are zero):

```json
{
  "requests": 2,
  "input_tokens": 1500,
  "cache_read_tokens": 900,
  "output_tokens": 200,
  "provider_receipts": 1,
  "cost": "0.0035",
  "unknown_cost_records": 0,
  "incomplete_requests": 0,
  "tool_calls": 1
}
```

The cache hit rate is 60%. Amounts are USD only: Python uses `Decimal`, JSON uses decimal strings, unknown cost is `null`, and known zero is `"0"`. A subtotal with unknown records is explicitly incomplete. Built-in pricing uses 80 significant decimal digits and half-even rounding; adding stored amounts preserves precision.

Each actual public Model invocation is measured even if output validation later retries, the caller discards the response, or execution fails or is cancelled. A dispatched request with no supplied usage has `usage_status="unavailable"` and unknown cost. Refused calls and unused lazy streams have no record. Hidden SDK retries cannot be individually measured without provider support.

A normal record has `revision=1`. Polls of the same suspended provider generation retain its record identity and owner and increment the version. Consumers aggregate the highest version once, not every delivered snapshot. Bounded response metadata carries this identity across resume; ordinary imported history creates no charge. A resumed generation whose price policy changes reports unknown cumulative cost. Repeating a delivery is safe; conflicting content at the same version fails explicitly.

Provider integrations call `AgentContext.record_provider_usage()`. Hosts can persist all sources through one optional collaborator:

```python
from a13n_harness import RunBindings
from a13n_harness.usage import UsageRecord

class UsageStore:
    async def report(self, records: tuple[UsageRecord, ...]) -> None:
        # Atomically insert by tenant, record_id and revision in your database.
        await save_usage_records(records)

bindings = RunBindings.embedded(usage_reporter=UsageStore())
```

No registration is needed before a call. Omitting the reporter still provides the complete embedded ledger. Reporting failures preserve local records and fail delivery; they never retry model execution. Cancellation shields cleanup for at most five seconds. Abrupt process loss before reporting can lose observations; this is not an invoice or zero-loss billing pipeline. Usage events and terminal records repeat the same facts and must not be charged separately.

Cumulative Run limits include auxiliary model requests and known provider cost. Pending in-memory reservations protect request limits under concurrency; token and cost limits are checked after capture and may be exceeded by calls already in flight. An unknown cost cannot prove a hard spending ceiling. The [pre-dispatch check](hosting.md#check-model-calls-before-dispatch) remains available for Host admission policy. Budget baselines do not become new Run usage.

Model-cost valuation is enabled by default. `HarnessBuilder` inserts `CatalogModelCostCapability`, which freezes the current valid pricing catalog for the built Agent. Without Host-enabled updates this is bundled `genai-prices` data plus Harness supplements. `get_default_pricing_catalog()` always reads that bundled baseline; `get_current_pricing_catalog()` additionally adopts successful upstream updates. Both return immutable catalogs without downloading anything. Read or export the current snapshot:

```python
from a13n_harness.pricing import get_current_pricing_catalog

pricing = get_current_pricing_catalog()
entry = pricing["openai:gpt-5.5"]
exported = pricing.model_dump(mode="json")
```

### Keep Prices Current in a Host

Pydantic AI 2.40 or later exposes `prices.update_in_background()`. Start it once in your final application process, not during import or before forking. The following sketch uses your application's `serve()` function:

```python
from pydantic_ai import prices

async def main():
    with prices.update_in_background():
        await serve()
```

The upstream updater downloads immediately and then hourly. Startup need not wait for the first download: bundled prices are usable immediately, and failed downloads retain the last good data. Every later `HarnessBuilder.build()` automatically captures validated updates without restarting or clearing a cache. An already built executable keeps its old prices even when reused; rebuild it to adopt updates. The same rule keeps an active run and its inline descendants stable.

In an async Host, capture the catalog off the event loop and pass it to the builder. The explicit snapshot is used for default pricing only; a custom model-cost Capability still wins:

```python
from anyio import to_thread
from a13n_harness import HarnessBuilder
from a13n_harness.pricing import get_current_pricing_catalog

catalog = await to_thread.run_sync(get_current_pricing_catalog)
executable = HarnessBuilder().build(definition, pricing_catalog=catalog)
```

Downloaded entries override packaged standard prices; missing entries keep bundled coverage. Because upstream `genai-prices` has no service-tier selector, packaged service-tier rules supplement refreshed standard entries. Their prices and provenance contribute to the effective revision. Explicit complete-entry overrides can still replace or remove those rules. Conversion failures retain the previous valid catalog. Identical downloaded pricing content keeps the same revision regardless of retrieval time. No price history or disk cache is created. Stopping the updater does not erase already downloaded prices; pass `get_default_pricing_catalog()` as `pricing_catalog` when a build must use bundled data regardless of other process activity.

### Price the Served Service Tier

Harness uses the **actual served tier** in `ModelResponse.provider_details`, not the request's `service_tier` setting. Pydantic AI 2.51.0 exposes this for OpenAI Chat/Responses (including streaming) and Gemini Developer API. A priority request downgraded to `default` is priced at standard rates. Cost is calculated on each response before native usage accumulation, including inherited child and auxiliary policies.

`ModelPriceRule.service_tier` selects an exact tier; omitted values describe standard pricing. The last active matching rule wins within that tier, independently of date/time conditions. Missing metadata retains legacy standard estimates; `default`, `standard`, and `on_demand` may use an untiered rule. Other tiers require an explicit matching rule: there is no universal discount/premium multiplier. OpenAI's `fast` response spelling selects its `priority` tariff. `max_input_tokens` bounds a tariff when the provider has not published prices above a context limit. Token-length cliffs remain `PriceComponent.tiers`, a separate dimension.

Unknown or unsupported tiers decline Harness valuation rather than silently applying standard rates. Existing upstream cost, if any, remains available with its original cost source; otherwise cost is unknown, not zero. Malformed served-tier metadata reports pricing failure without failing the Run. Missing tier metadata does **not** prove standard serving.

The bundled token-price coverage was checked against official tables on **September 26, 2026**:

| Provider                                                                                                                   | Public rules included                                                                                                                                                                                                                                                      | Important limits                                                                                                                                                                                                                                                                                                                |
| -------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [OpenAI](https://developers.openai.com/api/docs/pricing)                                                                   | 25 models in the union of Flex and Fast tables: GPT-6 Astra/Sol/Luna; GPT-5.6 Sol/Terra/Luna; GPT-5.5/Pro; GPT-5.4/Mini/Nano/Pro; GPT-5.2; GPT-5.1; GPT-5/Mini/Nano; GPT-4.1/Mini/Nano; GPT-4o/2024-05-13/Mini; o3; o4-mini                                                | Each model gets only its published tiers. [Fast](https://developers.openai.com/api/docs/guides/fast-mode) also uses the `priority` spelling. Published long-context rates start above 272,000 input tokens. GPT-5.5 Fast, GPT-5.4 Fast, and GPT-5.5 Pro Flex decline above that boundary instead of inventing rates.            |
| [Gemini Developer API](https://ai.google.dev/gemini-api/docs/pricing)                                                      | Standard, [Flex](https://ai.google.dev/gemini-api/docs/flex-inference), and [Priority](https://ai.google.dev/gemini-api/docs/priority-inference) for Gemini 3.8/3.7/3.6 Flash, 3.5 Flash/Flash-Lite, 3.1 Flash-Lite/Pro Preview, 3 Flash Preview, 2.5 Pro/Flash/Flash-Lite | Literal cache and audio prices are retained, including unchanged Flex cache prices for several models. Pro context cliffs start above 200,000 tokens. The 3.6–3.8 Flash introductory rates change January 1, 2027.                                                                                                              |
| [Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/priority-paygo)                                     | No automatic tier tariff                                                                                                                                                                                                                                                   | Actual `traffic_type` is passed as a lower-case tier identifier (for example `on_demand_priority`). Developer API tariffs are not borrowed for Vertex traffic. Author endpoint-specific rules in a custom policy.                                                                                                               |
| [Anthropic](https://platform.claude.com/docs/en/api/service-tiers) and [Groq](https://console.groq.com/docs/service-tiers) | No inferred nonstandard tariff                                                                                                                                                                                                                                             | Anthropic priority and Groq performance are capacity contracts, not a universal token surcharge; Groq Flex has on-demand prices. Native upstream adapters do not currently expose their served tier. Anthropic Fast uses a separate served `speed` dimension, also unavailable here. Request settings are not billing evidence. |

These are token-cost estimates, not invoice parity: regional uplifts, capacity commitments, storage duration, grounding, other product fees, and negotiated prices are not derived from a service tier. Bundled tier prices change with package updates, not with the upstream standard-price downloader.

For selected-Model `TokenPricingCapability` policies (including saved Service Model pricing), add tier rules to that complete entry. Existing standard-only entries are not silently replaced with public catalog rates; they decline nonstandard tiers until explicitly configured. The Console standard-price editor preserves authored tier rules.

### Override Pricing

To replace prices, create complete `ModelPricingEntry` values and pass a shallow update dictionary. Each value replaces the entire entry at that `provider:model` key and wins over downloaded and bundled prices; nested fields are not merged:

```python
from a13n_harness import HarnessBuilder
from a13n_harness.pricing import CatalogModelCostCapability

costs = CatalogModelCostCapability(
    pricing_updates={replacement.key: replacement},
)
executable = HarnessBuilder().build(
    spec,
    output_type=str,
    capabilities=(costs,),
)
```

One custom `AbstractModelCostCapability` supplied through build-time `capabilities=` atomically replaces the default. More than one is a definition error. Use `NoModelCostCapability()` to explicitly preserve only provider or upstream-library cost without Harness valuation. Inline child runs inherit the parent's selected policy so the shared usage tree is valued consistently; the same child definition uses its own build-time policy when executed independently.

Pricing failure or model lookup miss does not fail the Agent run. Usage records identify the pricing status, catalog revision, selected rule, and actual cost source. Durable aggregation, reconciliation, negotiated discounts, billing, and exporter delivery remain Host concerns.

## Design boundary

- Pydantic AI supplies request counters and local execution checks; the Harness ledger owns public summaries and cumulative limits.
- Harness attributes root, child, and provider work and captures a pricing policy for the executable.
- The Host owns durable aggregation, billing, negotiated prices, and whether to enable background catalog updates.

For metrics and traces rather than cost accounting, see [Observation](observation.md).
