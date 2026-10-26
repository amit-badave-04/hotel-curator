import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import { ANALYZE_OK, EVALUATION, meta } from '@/test/fixtures';
import { apiError } from '@/test/handlers';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

function region(name: string): HTMLElement {
  return screen.getByRole('region', { name });
}

async function openAbout() {
  const app = renderApp('/about');
  await screen.findByRole('heading', { level: 1, name: 'How hotel-curator works' });
  await screen.findByRole('region', { name: 'How tiers work' });
  return app;
}

describe('About page', () => {
  it('explains the method with the policy values', async () => {
    await openAbout();
    expect(region('How the labels were made')).toHaveTextContent('machine-made');
    const ladder = region('The model ladder');
    expect(ladder).toHaveTextContent('macro-F1');
    expect(ladder).toHaveTextContent('were scored the same way on the same test reviews');
    expect(ladder).toHaveTextContent(
      'models whose name ends in -train2 were trained on both labelled training sets (8,006 reviews), the others on the first set only (4,006 reviews)',
    );
    const tiers = region('How tiers work');
    expect(tiers).toHaveTextContent('at least 6 different months');
    expect(tiers).toHaveTextContent('Policy version 2026-10-24.1');
    const grades = region('What the grades mean');
    expect(grades).toHaveTextContent('At least 80% praise (about 4 praises per complaint)');
    expect(grades).toHaveTextContent('At most 5% complained (1 guest in 20)');
    expect(tiers).toHaveTextContent('A usable review is one the grades can rely on');
    expect(tiers).toHaveTextContent('near-duplicates');
    const limits = region('Limits and caveats');
    expect(limits).toHaveTextContent('Machine-made labels');
    expect(limits).toHaveTextContent('August 2015 to August 2017');
    expect(limits).toHaveTextContent('Licence unconfirmed');
    expect(
      within(region('For developers')).getByRole('link', { name: /API documentation/ }),
    ).toHaveAttribute('href', '/api/docs');
  });

  it('lists the tier rules in the order the API applies them', async () => {
    await openAbout();
    const table = within(region('How tiers work')).getByRole('region', {
      name: 'Tier rules (scrollable table)',
    });
    const rules = within(table).getAllByRole('row').slice(1);
    expect(rules).toHaveLength(6);
    expect(rules[0]).toHaveTextContent(/too few reviews.*Fewer than 30 usable reviews\./);
    expect(rules[1]).toHaveTextContent(/Caution.*graded Weak, and is still Weak by the stricter/);
    expect(rules[2]).toHaveTextContent(/too few opinions.*Fewer than 10 opinions on an essential/);
    expect(rules[3]).toHaveTextContent(/Excellent.*All three essentials Strong/);
    expect(rules[4]).toHaveTextContent(/Good.*At least two essentials Strong, no essential Weak/);
    expect(rules[5]).toHaveTextContent(
      /Fair.*an essential graded Weak that does not pass the stricter test for Caution/,
    );
  });

  it('shows the live policy values from /v1/meta, not the built-in defaults', async () => {
    const live = meta();
    live.policy = {
      ...live.policy,
      min_reviews: 40,
      min_opinions: 12,
      materiality: 0.04,
      two_sided: { strong: 0.75, weak: 0.5 },
      must_be_lines: { strong: 0.04, weak: 0.1 },
      excellent: { min_months: 8, max_month_share: 0.2 },
    };
    server.use(http.get('*/api/v1/meta', () => HttpResponse.json(live)));
    await openAbout();
    const tiers = region('How tiers work');
    expect(tiers).toHaveTextContent('Fewer than 40 usable reviews.');
    expect(tiers).toHaveTextContent('Fewer than 12 opinions on an essential.');
    expect(tiers).toHaveTextContent(
      'at least 8 different months with no month holding more than 20% of them',
    );
    expect(tiers).toHaveTextContent('raised by at least 4% of guests');
    const grades = region('What the grades mean');
    expect(grades).toHaveTextContent('At least 75% praise (about 3 praises per complaint)');
    expect(grades).toHaveTextContent('At most 4% complained (1 guest in 25)');
    expect(grades).toHaveTextContent('Fewer than 12 opinions');
    expect(grades).toHaveTextContent('Fewer than 40 reviews');
  });

  it('says when no evaluation is available', async () => {
    await openAbout();
    expect(region('Evaluation')).toHaveTextContent('Evaluation not available');
  });

  it('shows the evaluation with intervals, the difference and a table of all models', async () => {
    server.use(http.get('*/api/v1/meta', () => HttpResponse.json(meta(EVALUATION))));
    await openAbout();
    const evaluation = region('Evaluation');
    await waitFor(() => expect(evaluation).toHaveTextContent('Served model, macro-F1'));
    expect(evaluation).toHaveTextContent('0.712');
    expect(evaluation).toHaveTextContent('95% interval 0.681 to 0.741');
    expect(evaluation).toHaveTextContent('+0.108');
    expect(evaluation).toHaveTextContent('credibly better than v1');
    expect(evaluation).toHaveTextContent('0.031');
    const table = within(evaluation).getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(within(table).getByRole('rowheader', { name: 'encoder-small (served)' })).toBeVisible();
    expect(within(table).getByRole('rowheader', { name: 'v1-parts-tuned (v1)' })).toBeVisible();
    expect(evaluation).toHaveTextContent('marked "(served)".');
    expect(evaluation).not.toHaveTextContent('ONNX package');
  });

  it("explains when the served package's score differs from its checkpoint's", async () => {
    const evaluation = { ...EVALUATION, macro_f1: { point: 0.709, low: 0.679, high: 0.739 } };
    server.use(http.get('*/api/v1/meta', () => HttpResponse.json(meta(evaluation))));
    await openAbout();
    const section = region('Evaluation');
    await waitFor(() => expect(section).toHaveTextContent('95% interval 0.679 to 0.739'));
    // v1's stat is v1's own score, so the note names only the package's numbers.
    expect(section).toHaveTextContent(
      "The served model is an ONNX package built from the encoder-small checkpoint. The served model's numbers above (macro-F1, the difference from v1 and the calibration error) are the package's own.",
    );
    expect(section).not.toHaveTextContent('The numbers above are');
    expect(section).toHaveTextContent(
      "the served model's row there reads 0.712 (95% interval 0.681 to 0.741)",
    );
    expect(section).toHaveTextContent('marked "(served, checkpoint)".');
    const table = within(section).getByRole('table');
    expect(
      within(table).getByRole('rowheader', { name: 'encoder-small (served, checkpoint)' }),
    ).toBeVisible();
  });
});

describe('Try the model', () => {
  async function submit(liked: string, disliked = '') {
    const app = await openAbout();
    const panel = region('Try the model');
    if (liked) await app.user.type(within(panel).getByLabelText('What the guest liked'), liked);
    if (disliked) {
      await app.user.type(within(panel).getByLabelText('What the guest disliked'), disliked);
    }
    await app.user.click(within(panel).getByRole('button', { name: 'Analyse review' }));
    return { ...app, panel };
  }

  it('shows the labels with calibrated probabilities', async () => {
    let body: unknown = null;
    server.use(
      http.post('*/api/v1/analyze', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(ANALYZE_OK);
      }),
    );
    const { panel } = await submit('Friendly staff and spotless room', 'Street noise at night');
    expect(await within(panel).findByText('What the model found')).toBeInTheDocument();
    // A status region that was on the page before the result announces it.
    expect(within(panel).getByRole('status')).toHaveTextContent('Analysis done: 3 labels found.');
    expect(body).toEqual({
      liked: 'Friendly staff and spotless room',
      disliked: 'Street noise at night',
    });
    const items = within(panel).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('StaffPraise93% likely');
    expect(items[1]).toHaveTextContent('CleanlinessPraise81% likely');
    expect(items[2]).toHaveTextContent('NoiseComplaint88% likely');
    expect(panel).toHaveTextContent('Model: test-model');
  });

  it('explains that the model is unavailable on 503', async () => {
    const { panel } = await submit('Nice staff');
    const alert = await within(panel).findByRole('alert');
    expect(alert).toHaveTextContent('The model is not available right now');
    expect(alert).toHaveTextContent('The model is not loaded.');
    expect(alert).toHaveTextContent('Search, hotel pages and compare still work');
    expect(alert).toHaveTextContent('req-503-test');
  });

  it('shows the wait time on 429', async () => {
    server.use(
      http.post('*/api/v1/analyze', () =>
        apiError(
          429,
          'rate_limited',
          'Too many requests; try again later.',
          {},
          {
            'Retry-After': '42',
          },
        ),
      ),
    );
    const { panel } = await submit('Nice staff');
    const alert = await within(panel).findByRole('alert');
    expect(alert).toHaveTextContent('Too many requests');
    expect(alert).toHaveTextContent('Try again in 42 seconds.');
  });

  it('shows the validation message and fields on 422', async () => {
    server.use(
      http.post('*/api/v1/analyze', () =>
        apiError(422, 'invalid_request', 'The request is invalid.', {
          fields: [
            { location: ['body', 'liked'], message: 'String should have at most 2000 characters' },
          ],
        }),
      ),
    );
    const { panel } = await submit('Nice staff');
    const alert = await within(panel).findByRole('alert');
    expect(alert).toHaveTextContent('The text could not be analysed');
    expect(alert).toHaveTextContent('The request is invalid.');
    expect(alert).toHaveTextContent('liked: String should have at most 2000 characters');
  });

  it('checks for empty input before calling the API and counts characters', async () => {
    let called = false;
    server.use(
      http.post('*/api/v1/analyze', () => {
        called = true;
        return HttpResponse.json(ANALYZE_OK);
      }),
    );
    const { panel, user } = await submit('');
    expect(within(panel).getByRole('alert')).toHaveTextContent(
      'Write something in at least one of the two boxes.',
    );
    expect(called).toBe(false);

    // The error is tied to both boxes, and focus goes to the first one.
    const liked = within(panel).getByLabelText('What the guest liked');
    const disliked = within(panel).getByLabelText('What the guest disliked');
    expect(liked).toHaveFocus();
    expect(liked).toHaveAttribute('aria-invalid', 'true');
    expect(disliked).toHaveAttribute('aria-invalid', 'true');
    expect(liked).toHaveAccessibleDescription(
      'Write something in at least one of the two boxes. 0 of 2,000 characters',
    );
    expect(liked).toHaveAttribute('maxlength', '2000');

    // Typing clears the error.
    await user.type(liked, 'Quiet');
    expect(liked).toHaveAccessibleDescription('5 of 2,000 characters');
    expect(liked).not.toHaveAttribute('aria-invalid');
    expect(within(panel).queryByRole('alert')).not.toBeInTheDocument();
  });
});
