import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';

import type { HotelDetail } from '@/api/generated/model';
import { foxtrot } from '@/test/fixtures';
import { renderApp } from '@/test/render';
import { server } from '@/test/server';

async function openHotel(id: string) {
  const app = renderApp(`/hotels/${id}`);
  await screen.findByRole('heading', { level: 1, name: /House|Grand|Inn|Rooms|Stay/ });
  return app;
}

function section(name: string): HTMLElement {
  return screen.getByRole('region', { name });
}

function card(heading: string): HTMLElement {
  return screen.getByRole('heading', { name: heading }).closest('div')!.parentElement!;
}

function serveHotel(hotel: HotelDetail) {
  server.use(http.get('*/api/v1/hotels/:hotelId', () => HttpResponse.json(hotel)));
}

describe('Hotel page', () => {
  it('shows the tier, label, verdict, essentials, what would change it and for you', async () => {
    await openHotel('h_00000000c3');
    expect(screen.getByRole('heading', { level: 1, name: 'Charlie House' })).toBeVisible();
    await waitFor(() => expect(document.title).toBe('Charlie House | hotel-curator'));
    expect(screen.getByText('Good, but noisy')).toBeInTheDocument();
    expect(
      screen.getByText(
        'In reviews from Sep 2015 to Aug 2017, guests praised cleanliness, room and staff (confirmed).',
      ),
    ).toBeInTheDocument();

    const essentials = screen
      .getByRole('heading', { name: 'The three essentials' })
      .closest('div')!.parentElement!;
    expect(within(essentials).getByText(/^Cleanliness: Strong\./)).toBeInTheDocument();
    expect(within(essentials).getByText(/^Room: Strong\./)).toBeInTheDocument();
    expect(within(essentials).getByText(/^Staff: Strong\./)).toBeInTheDocument();

    expect(
      screen.getByText('Complaints about noise (40% of guests) hold it back.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText("If noise doesn't bother you, this hotel would be Excellent."),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Based on 120 usable reviews of 120 from Sep 2015 to Aug 2017.'),
    ).toBeInTheDocument();
  });

  it('says "No catch to set aside" only when the hotel has no catch', async () => {
    await openHotel('h_00000000a1');
    expect(card('For you')).toHaveTextContent(/^For youNo catch to set aside\.$/);
  });

  it('says a catch does not change the tier when setting it aside would not', async () => {
    // Fair for want of Strong essentials, with one catch: Good allows one
    // catch, so ignoring it changes nothing and the API sends no "for you".
    serveHotel(foxtrot);
    await openHotel(foxtrot.hotel_id);
    expect(screen.getByText('Catch:').parentElement).toHaveTextContent('Catch: noise');
    expect(card('The three essentials')).not.toHaveTextContent('Strong');
    expect(card('What would change it')).not.toHaveTextContent('noise');
    const forYou = card('For you');
    expect(forYou).toHaveTextContent('Setting aside the catch (noise) would not change the tier.');
    expect(forYou).not.toHaveTextContent('No catch to set aside');
    // The Fair meaning allows a Weak essential (ADR 006, erratum E1).
    expect(screen.getByText(/^Short of Good: /)).toHaveTextContent(
      'an essential is graded Weak but does not pass the stricter test for Caution',
    );
    expect(screen.queryByText(/No essential is weak/)).not.toBeInTheDocument();
  });

  it('lists all eight attributes with grade, observed share, likely range and trend', async () => {
    await openHotel('h_00000000c3');
    const table = within(section('All eight attributes')).getByRole('table');
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(9); // header + 8 attributes

    const noise = within(table).getByRole('rowheader', { name: 'Noise' }).closest('tr')!;
    expect(within(noise).getByText('Weak')).toBeInTheDocument();
    expect(within(noise).getByText('40%')).toBeInTheDocument();
    expect(within(noise).getByText('complained')).toBeInTheDocument();
    expect(within(noise).getByText('33% to 47%')).toBeInTheDocument();
    expect(within(noise).getByText('Improved')).toBeInTheDocument();
    expect(within(noise).getByText('Last 6 months 30% complaints, before 45%')).toBeInTheDocument();

    const room = within(table).getByRole('rowheader', { name: /Room/ }).closest('tr')!;
    expect(within(room).getByText('Declined')).toBeInTheDocument();

    // Counts are expected counts: rounded one by one and marked "about".
    expect(noise).toHaveTextContent('48 complaints in 120 reviews');
    expect(room).toHaveTextContent('About 118 opinions (115 praises, 2 complaints)');

    const wifi = within(table).getByRole('rowheader', { name: 'Wifi' }).closest('tr')!;
    expect(within(wifi).getByText('OK, leaning strong')).toBeInTheDocument();

    const food = within(table).getByRole('rowheader', { name: 'Breakfast & food' }).closest('tr')!;
    expect(within(food).getByText('Too few opinions')).toBeInTheDocument();
    expect(within(food).getByText('No trend data')).toBeInTheDocument();
  });

  it('labels supporting quotes and counter-examples, with month and traveler type only', async () => {
    const { user } = await openHotel('h_00000000c3');
    const evidence = section('Evidence by attribute');
    expect(evidence).toHaveTextContent('The data publisher removed punctuation');

    // Cleanliness is the first tab: one quote for and one against.
    expect(
      within(evidence).getByRole('heading', { name: /Supporting quotes/ }),
    ).toBeInTheDocument();
    expect(within(evidence).getByRole('heading', { name: /Counter-examples/ })).toBeInTheDocument();
    const counter = within(evidence).getByText('Hair in the shower on arrival').closest('figure')!;
    expect(counter).toHaveTextContent('From what the guest disliked · Mar 2017 · Friends');

    await user.click(within(evidence).getByRole('tab', { name: 'Noise' }));
    const noiseQuote = await within(evidence).findByText('Street noise at night');
    expect(noiseQuote.closest('figure')).toHaveTextContent(
      'From what the guest disliked · Aug 2017',
    );
    expect(within(evidence).getByText('No clear counter-example.')).toBeInTheDocument();

    await user.click(within(evidence).getByRole('tab', { name: 'Breakfast & food' }));
    expect(
      await within(evidence).findByText(/No clear quote: no review part about breakfast & food/),
    ).toBeInTheDocument();
  });

  it('shows who the hotel suits from segments', async () => {
    await openHotel('h_00000000c3');
    const suits = section('Who it suits');
    expect(
      within(suits).getByText(
        'Families complained about noise more often than other guests (an estimated 55% vs 35% of reviews).',
      ),
    ).toBeInTheDocument();
    const row = within(suits).getByRole('cell', { name: 'families' }).closest('tr')!;
    expect(row).toHaveTextContent('Less happy');
    expect(row).toHaveTextContent('55%');
    expect(row).toHaveTextContent('35%');
    // Noise is judged by its complaint rate, which counts reviews.
    expect(row).toHaveTextContent('40 reviews, 97% sure');
  });

  it('shows usable reviews per month as a chart with a table of the same numbers', async () => {
    await openHotel('h_00000000a1');
    const volume = section('Reviews over time');
    expect(volume).toHaveTextContent(
      '120 usable reviews over 24 months; the busiest month is Jul 2016 with 10.',
    );
    const table = within(volume).getByRole('table');
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(25); // header + 24 months, the empty one included
    expect(rows[4]).toHaveTextContent(/^Dec 20150$/);
  });

  it('leaves out the volume section when the API sends no months', async () => {
    await openHotel('h_00000000c3');
    expect(screen.queryByRole('heading', { name: 'Reviews over time' })).not.toBeInTheDocument();
  });

  it("marks the booking site's score as for reference, not used, and shows provenance", async () => {
    await openHotel('h_00000000c3');
    const score = section("The booking site's own score");
    expect(score).toHaveTextContent('For reference, not used');
    expect(score).toHaveTextContent('8.0 out of 10');
    expect(score).toHaveTextContent('never read this score');

    const footer = screen.getByRole('heading', { name: 'Where this comes from' }).parentElement!;
    expect(footer).toHaveTextContent('2026-10-24.1');
    expect(footer).toHaveTextContent('test-model');
    expect(footer).toHaveTextContent('Booking.com 515K hotel reviews');
    expect(footer).toHaveTextContent('unconfirmed; legal review required');
    expect(footer).toHaveTextContent('See NOTICE');
  });

  it('handles missing trends, quotes and site score for a hotel with too few reviews', async () => {
    await openHotel('h_00000000d4');
    expect(screen.getAllByText('Not enough evidence to judge').length).toBeGreaterThan(0);
    expect(screen.getByText('(Not enough reviews)')).toBeInTheDocument();
    expect(screen.queryByText('Caution')).not.toBeInTheDocument();
    expect(
      screen.getByText('18 more reviews are needed before a tier is given.'),
    ).toBeInTheDocument();
    expect(section("The booking site's own score")).toHaveTextContent(
      'did not publish an average score',
    );
    expect(section('Who it suits')).toHaveTextContent('No credible difference');
    expect(within(section('All eight attributes')).getAllByText('No trend data')).toHaveLength(8);
  });

  it('shows not found with the request id for an unknown hotel', async () => {
    renderApp('/hotels/h_ffffffffff');
    expect(await screen.findByRole('heading', { name: 'Hotel not found' })).toBeInTheDocument();
    expect(screen.getByText('req-404-test')).toBeInTheDocument();
  });

  it('does not call the API for a malformed hotel id', async () => {
    let called = false;
    server.use(
      http.get('*/api/v1/hotels/:hotelId', () => {
        called = true;
        return HttpResponse.json({});
      }),
    );
    renderApp('/hotels/not-an-id');
    expect(await screen.findByRole('heading', { name: 'Hotel not found' })).toBeInTheDocument();
    expect(called).toBe(false);
    expect(screen.queryByText(/Request id/)).not.toBeInTheDocument();
  });

  it('explains "usable review" in a popover that opens on click', async () => {
    const { user } = await openHotel('h_00000000c3');
    const trigger = screen.getByRole('button', { name: 'What is a usable review?' });
    await user.click(trigger);
    const popup = await screen.findByRole('dialog', { name: 'What is a usable review?' });
    expect(popup).toHaveTextContent('near-duplicate reviews are left out');
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });

  it('names noise and wifi strengths so they do not read as "noisy"', async () => {
    await openHotel('h_00000000a1');
    expect(screen.getByText(/quiet \(few noise complaints\)/)).toBeInTheDocument();
  });

  it('adds the hotel to the compare list', async () => {
    const { user } = await openHotel('h_00000000c3');
    await user.click(screen.getByRole('button', { name: /Add to compare/ }));
    expect(screen.getByRole('button', { name: /Remove from compare/ })).toBeInTheDocument();
  });
});
