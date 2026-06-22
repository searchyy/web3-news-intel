# Feishu Setup

## Enterprise Application Bot Mode

1. Create a Feishu/Lark self-built enterprise application.
2. Enable bot capability for the application.
3. Grant the minimum permissions required to send chat messages and receive the selected group/card callbacks.
4. Configure callback URLs:
   - `PUBLIC_BASE_URL/integrations/feishu/events`
   - `PUBLIC_BASE_URL/integrations/feishu/card-actions`
5. Store the app credentials in environment variables or a secret manager:
   - `FEISHU_APP_ID`
   - `FEISHU_APP_SECRET`
   - `FEISHU_VERIFICATION_TOKEN`
   - `FEISHU_ENCRYPT_KEY`
6. Publish/install the application.
7. Add the bot to one test group.
8. Open the administration panel and approve the pending group.
9. Configure a routing rule.
10. Send one test card.
11. Set `FEISHU_ENABLED=true` and `FEISHU_SEND_ENABLED=true` only after the test is verified.

`FEISHU_SEND_ENABLED` defaults to `false`. With the default setting the system can render cards and create dry-run delivery records, but it will not contact Feishu.

## Custom Webhook Mode

Custom webhook mode is outbound-only compatibility mode. It cannot receive group membership events or card action callbacks, so it should not be the primary production mode.

Webhook URLs are encrypted at rest with `FIELD_ENCRYPTION_KEY`. The full URL is write-only and is never returned by the API after creation.

Supported webhook hosts are restricted to Feishu/Lark public hosts and remain subject to URL safety checks.

## Anti-Flood Boundary

Approving or enabling a new group does not publish historical events. The router only sends events whose `first_seen_at` is greater than or equal to the destination `activated_at`. Historical backfill must be a separate explicit administrator action.
