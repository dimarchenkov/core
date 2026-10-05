# Temporary AQSI itemized sale spike

This experiment is not the Sales implementation. No Core sale, inventory movement,
price history entry, or catalog publication is created. Do not treat HTTP acceptance
as proof of payment or fiscalization.

## Official mechanism

Sources: https://api.aqsi.ru/ and https://aqsi.ru/support/zakazyi/.

- POST `/pub/v2/Orders/simple`: send an itemized deferred order.
- GET `/pub/v2/Orders/simple/{orderId}`: inspect the order and associated receipts.
- The configured `device` binds the order to the intended cash register.
- On the register, use «Отложенные заказы», select the order, inspect positions,
  then use «Оплатить» and the normal payment-method selection.
- Payment and fiscalization happen on AQSI. The physical itemized receipt remains
  the acceptance criterion; unit tests cannot establish that the device is ready.

The second, direct path uses the current AQSI v4 device-operation contract:

- POST `/pub/v4/Slips/process/purchase` starts acquiring with an integer amount in
  kopecks and `mode=card_only`.
- GET `/pub/v4/Operations/{operationId}` returns the documented states `Pending`,
  `Processing`, `Finishing`, `Completed`, `Canceled`, `Timeout`, or `Error`.
- Only `Completed` is treated as acquiring success. Its `result` is a JSON string
  containing the completed Slip entity.
- POST `/pub/v4/Receipts/process` starts fiscalization. AQSI's receipt payment model
  explicitly accepts that complete Slip object alongside payment type `1` (cashless)
  and the amount in kopecks. This is the documented linkage used by the spike.
- The public schema supports this acquiring-to-receipt composition. Physical acceptance has
  now confirmed the composition on the real device; the spike still is not a permanent Sales
  architecture.

## Local configuration

The page is `/dev/aqsi-sale-spike`. Sign in to `/app` as Administrator in the same
browser tab first, then navigate to the spike page (authentication uses sessionStorage).

Set these values in local `.env`, not repository defaults:

```dotenv
CORE_AQSI_SALE_SPIKE_ENABLED=true
CORE_AQSI_SALE_SPIKE_DEVICE_ID=<verified AQSI device identifier>
CORE_AQSI_SALE_SPIKE_TAX_SYSTEM_CODE=<verified receipt tax system code>
# Optional; default 120 seconds, allowed 30–300 seconds.
CORE_AQSI_SALE_SPIKE_OPERATION_TTL_MS=120000
```

The existing `CORE_AQSI_API_KEY`, base URL, timeout, and `CORE_AQSI_TAX_CODE` are reused.
The direct action refuses to start unless the numeric v4 device ID and taxation system
are explicit. The physically completed Pending Order receipt was inspected read-only and
reported tax system code `2` (УСН доход); that value belongs in local `.env`, never in a
repository default. Default state is disabled. Recreate the API container after changes.

## Request mapping

The request uses an external `CORE-SPIKE-…` ID and number, UTC `dateTime`, configured
`device`, requested status `Отложен`, and `content.positions` containing one entry per
selected Variant. Each position carries Product plus meaningful Variant title as
`text`, Variant `sku` and `barcodes`, integer `quantity`, and temporary unit `price`.
AQSI derives position amounts from quantity and unit price; the spike computes its
expected total with Decimal. Conversion to JSON numeric price is at the adapter boundary.

Fiscal mapping follows the existing goods integration: configured `tax` (default 6,
no VAT), `paymentMethodType=4` (full payment), `paymentSubjectType=1` (goods),
`unit=Штука`, `unitCode=0`, and `content.type=1` (income). These experimental values
are suitable only for the selected ordinary goods. Confirm the configured tax before
the physical experiment. Marked/excisable/agent goods are outside this spike.
No `checkClose` or payments are supplied; payment is selected on the physical register.

## Safety and limitations

- Explicit checkbox plus confirmation dialog; Enter only submits catalog search.
- Maximum three existing active Variants, temporary positive kopeck prices, quantities 1–100.
- Search uses the existing targeted catalog search API.
- The started operation locks sending; its reference is saved in sessionStorage before
  the request so a lost response or page reload retains a status-check target.
- Server duplicate protection is process-local. It is not durable across restarts or
  multiple workers. Use this as a single-process supervised experiment only.
- No automatic retry, payment, refund, worker, or webhook.
- A timeout is an unknown outcome: inspect the reference on AQSI before any new attempt.
- Reference and sanitized error code are logged; API secrets are not logged.
- The API response describes request acceptance, not a confirmed device/payment state.
- GET status displays the actual AQSI projection, including receipts if returned.
- A separate tab/browser can start a separate experiment; there is no permanent order
  registry in Core. Keep one operator/tab for the physical test.
- Direct checkout snapshots resolved Variant facts, temporary prices, quantities, and
  exact total before acquiring. Pricing and Inventory are never mutated or reread for
  receipt construction.
- The server creates one acquiring operation per `CORE-DIRECT-…` request ID. A timeout
  or missing operation ID is an unknown outcome and is never automatically resubmitted.
- A TCP connection timeout is distinguished from a response/read timeout. When Core
  cannot connect before sending any HTTP request, the UI reports that payment was not
  started and permits a separately confirmed attempt after connectivity is restored.
- The receipt is submitted once and only after acquiring reaches `Completed`. If receipt
  submission times out, it is not retried automatically because duplicate fiscalization
  safety is not established.
- `Canceled`, `Timeout`, and `Error` are final payment failures. `Finishing` is not treated
  as success even though AQSI says another command can be sent; the spike waits for
  `Completed` before fiscalization.
- Acquiring success followed by receipt failure is shown as a critical partial failure:
  payment, purchase operation, Slip, amount, receipt operation/error remain visible and
  the operator is warned not to pay again. No automatic refund, reversal, or retry exists.
- Direct attempt state and duplicate guards are process-local. Restarting Core or using
  multiple workers loses that state. After a restart, do not repeat payment; inspect AQSI.
- The operation result Slip is held only in process memory to form the receipt. Card or
  acquiring details are not returned to the browser or written to logs.

## Physical acceptance

1. Configure the verified device and enable the spike; rebuild/recreate API as needed.
2. Sign in as Administrator and open `/dev/aqsi-sale-spike` in the same tab.
3. Search and select 2–3 real Variants. Set quantities to 1 and test prices, e.g.
   10, 15, and 25 RUB. Confirm the displayed 50 RUB total.
4. Confirm and send once. Record Core reference and returned AQSI GUID.
5. Open «Отложенные заказы» on AQSI and find that reference. Verify all individual
   names, quantities, prices, and total before paying.
6. Use «Оплатить», select the intended payment method, and make the small real payment.
7. Verify the fiscal receipt lists separate positions and that their sum equals both
   the receipt total and actual payment. Use the spike's status check afterwards.

For the direct path:

1. Confirm the configured device is online, in command-waiting mode, has an open shift,
   and local tax system code is correct.
2. Open `/dev/aqsi-sale-spike`, choose two or three ordinary unmarked Variants, and use
   test prices totalling approximately 30–50 RUB.
3. Check the acknowledgement, click `⚡ ОПЛАТИТЬ КАРТОЙ НА AQSI`, read the dedicated
   real-payment warning, then click `Начать оплату` exactly once.
4. Without opening any AQSI menu, verify the terminal shows the exact amount and asks for
   a card. Tap a real card once.
5. Keep the Core page open while it polls approximately once per second. It must progress
   through the real AQSI states, then submit fiscalization after acquiring `Completed`.
6. Verify AQSI prints one receipt containing the same individual names, quantities, and
   temporary prices, with electronic payment equal to the total.
7. Core must finish with both purchase and receipt operations in `Completed`.
8. If Core says the status is unknown, or says payment succeeded but receipt failed, do
   not click Pay again. Record both operation IDs and inspect AQSI/support first.

Sending creates a real AQSI order. Paying on the register can create a real payment
and fiscal receipt. Any reversal is a separate manual AQSI operation.

## Verification so far

Both paths were physically accepted with real Core Variants and an itemized fiscal receipt.

- **Pending Order:** Core positions reached AQSI, the operator selected the deferred order on the
  physical register, paid through the normal AQSI flow, and received an itemized receipt whose
  total matched the basket. This proves the Core → AQSI fiscal-position mapping. It remains a
  valid option for pre-created, remote or pickup orders, but the required menu navigation makes
  it unsuitable as the preferred everyday in-store checkout.
- **Direct checkout:** Core initiated card acquiring on AQSI without manual register-menu
  navigation, the customer paid, Core triggered fiscalization, and AQSI printed the matching
  itemized receipt. This is the preferred target direction for ordinary in-store Sales/POS.

Automated mock coverage still protects exact kopeck amount/device, duplicate handling, immutable
basket receipt mapping, payment/receipt equality, partial failure, unknown outcomes, explicit tax
configuration and official HTTP paths. No automated test itself performs a real payment. The
temporary spike remains experimental: it has no durable Sale, Cart, payment state or Inventory
SALE movement and must not be presented as the production Sales domain.

Both paths depend on the AQSI cloud API and Internet connectivity. Offline/AQSI-unavailable
behavior, refunds/reversals and durable unknown-outcome recovery remain open Sales decisions.
