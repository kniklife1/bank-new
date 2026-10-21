import test from 'node:test';
import assert from 'node:assert/strict';
import {
  KEYWORD_CATEGORIES,
  DEFAULT_MAX_NOTIFICATIONS_PER_RUN,
  matchNoticeKeywords,
} from './keywordMatcher';

test('all 30 required keywords across 5 categories match accurately', () => {
  assert.equal(DEFAULT_MAX_NOTIFICATIONS_PER_RUN, 10);
  const categories = Object.keys(KEYWORD_CATEGORIES);
  assert.deepEqual(categories, [
    'Recruitment',
    'Examination',
    'Results',
    'Answer Keys',
    'Next Stages',
  ]);

  for (const [category, keywords] of Object.entries(KEYWORD_CATEGORIES)) {
    for (const kw of keywords) {
      const res = matchNoticeKeywords(`Official Notice for ${kw.toUpperCase()} 2026`);
      assert.equal(res.matched, true, `Expected '${kw}' in ${category} to match`);
      assert.equal(res.category, category);
    }
  }
});

test('handles case-insensitivity and spacing variations', () => {
  assert.equal(matchNoticeKeywords('Download Admit-Card / AdmitCard for PO').matched, true);
  assert.equal(matchNoticeKeywords('Category-wise Cut-Off and ScoreCard').matched, true);
  assert.equal(matchNoticeKeywords('Provisional   AnswerKey & Response-Sheet').matched, true);
  assert.equal(matchNoticeKeywords('Notice No. 12/2026', 'Schedule of DV and PET/PST').matched, true);
});

test('rejects false matches and non-recruitment noise', () => {
  assert.equal(
    matchNoticeKeywords('Corrigendum to Advt. No. 02 for Advisory Services').matched,
    false
  );
  assert.equal(
    matchNoticeKeywords('Competitive Bidding for Carpet Cleaning').matched,
    false
  );
  assert.equal(
    matchNoticeKeywords('Postponement of Postal Ballot Meeting').matched,
    false
  );
  assert.equal(
    matchNoticeKeywords('Apply Online for Home Loan at 8.35%').matched,
    false
  );
});
