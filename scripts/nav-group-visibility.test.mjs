import { test } from 'node:test';
import assert from 'node:assert/strict';
import { _groupShouldBeVisible } from '../app/static/nav-group-visibility.js';

test('group is visible when at least one item is shown', () => {
  assert.equal(_groupShouldBeVisible(['none', '', 'none']), true);
});

test('group is hidden when every item is display:none', () => {
  assert.equal(_groupShouldBeVisible(['none', 'none', 'none']), false);
});

test('group is hidden when given an empty list', () => {
  assert.equal(_groupShouldBeVisible([]), false);
});

test('an empty-string display value counts as visible', () => {
  assert.equal(_groupShouldBeVisible(['']), true);
});
