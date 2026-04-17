// Post-purchase attribution survey for Pinaka Jewellery.
//
// Target: purchase.thank-you.block.render (renders on thank-you page)
// Submits to: /api/attribution/submit on the Railway app.
//
// Single submit per order enforced by the server-side UNIQUE constraint.
// localStorage guard prevents re-render on refresh.

import '@shopify/ui-extensions/preact';
import { render } from 'preact';
import { useState } from 'preact/hooks';

const API = 'https://pinaka-agents-production-198b5.up.railway.app/api/attribution/submit';

export default async () => {
  render(<Survey />, document.body);
};

function extractOrderId(gidOrId) {
  // Shopify extension API gives order.id as a GID: gid://shopify/Order/123456
  // Our webhook/db uses the numeric part only.
  if (!gidOrId) return '';
  const s = String(gidOrId);
  const match = s.match(/(\d+)(?:\?.*)?$/);
  return match ? match[1] : s;
}

function Survey() {
  const order = shopify.order?.value;
  const orderId = extractOrderId(order?.id);
  const email = order?.email || order?.customer?.email || '';

  const storageKey = orderId ? `pinaka_attr_${orderId}` : '';
  const alreadySubmitted = storageKey && typeof localStorage !== 'undefined' && localStorage.getItem(storageKey);

  const [channel, setChannel] = useState('');
  const [channelDetail, setChannelDetail] = useState('');
  const [reason, setReason] = useState('');
  const [anniversaryDate, setAnniversaryDate] = useState('');
  const [status, setStatus] = useState(alreadySubmitted ? 'done' : 'idle');
  // status: 'idle' | 'sending' | 'done' | 'error'

  const needsDetail = channel === 'other' || channel === 'press' || channel === 'friend';
  const askForDate = reason === 'anniversary' || reason === 'engagement' || reason === 'milestone';

  function defaultRelationshipFor(r) {
    if (r === 'anniversary') return 'wedding_anniversary';
    if (r === 'engagement') return 'engagement';
    if (r === 'milestone') return 'milestone';
    return null;
  }

  async function submit() {
    if (!channel || !orderId) return;
    setStatus('sending');
    try {
      const resp = await fetch(API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          shopify_order_id: orderId,
          customer_email: email,
          channel_primary: channel,
          channel_detail: channelDetail.trim() || null,
          purchase_reason: reason || null,
          anniversary_date: askForDate && anniversaryDate ? anniversaryDate : null,
          relationship: askForDate ? defaultRelationshipFor(reason) : null,
        }),
      });
      if (resp.ok) {
        if (storageKey) localStorage.setItem(storageKey, '1');
        setStatus('done');
      } else {
        setStatus('error');
      }
    } catch (e) {
      setStatus('error');
    }
  }

  if (!orderId) {
    // Shouldn't happen on thank-you target, but fail silent rather than spam.
    return null;
  }

  if (status === 'done') {
    return (
      <s-banner tone="success" heading="Thank you">
        <s-text>Every bit helps us get to you better.</s-text>
      </s-banner>
    );
  }

  return (
    <s-stack direction="vertical" gap="base-loose">
      <s-heading>One small favor.</s-heading>
      <s-text>This helps us focus on the channels that actually bring you to us. Thirty seconds.</s-text>

      <s-choice-list
        name="channel"
        heading="Where did you first hear about Pinaka?"
        variant="single"
        value={channel}
        onChange={(event) => setChannel(event.target.value)}
      >
        <s-choice value="instagram">Instagram</s-choice>
        <s-choice value="tiktok">TikTok</s-choice>
        <s-choice value="google_search">Google search</s-choice>
        <s-choice value="pinterest">Pinterest</s-choice>
        <s-choice value="meta_ads">A Facebook or Instagram ad</s-choice>
        <s-choice value="friend">A friend or family</s-choice>
        <s-choice value="press">Press / podcast</s-choice>
        <s-choice value="other">Somewhere else</s-choice>
      </s-choice-list>

      {needsDetail && (
        <s-text-field
          label={
            channel === 'friend'
              ? 'Who referred you? (optional)'
              : channel === 'press'
              ? 'Which publication or podcast?'
              : 'Tell us more'
          }
          value={channelDetail}
          onChange={(event) => setChannelDetail(event.target.value)}
          maxLength={200}
        />
      )}

      <s-choice-list
        name="reason"
        heading="This piece is for…"
        variant="single"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
      >
        <s-choice value="self_purchase">Myself</s-choice>
        <s-choice value="gift">A gift</s-choice>
        <s-choice value="anniversary">Anniversary</s-choice>
        <s-choice value="engagement">Engagement</s-choice>
        <s-choice value="milestone">A milestone</s-choice>
        <s-choice value="other">Other</s-choice>
      </s-choice-list>

      {askForDate && (
        <s-stack direction="vertical" gap="tight">
          <s-text-field
            label="Special date? (so we can mark it with you next year)"
            type="date"
            value={anniversaryDate}
            onChange={(event) => setAnniversaryDate(event.target.value)}
          />
          <s-text variant="subdued">Optional — only used for a personal anniversary note, never to spam you.</s-text>
        </s-stack>
      )}

      <s-button
        variant="primary"
        onClick={submit}
        disabled={!channel || status === 'sending'}
      >
        {status === 'sending' ? 'Sending…' : 'Send'}
      </s-button>

      {status === 'error' && (
        <s-banner tone="critical">
          <s-text>Could not send. Try again?</s-text>
        </s-banner>
      )}
    </s-stack>
  );
}
