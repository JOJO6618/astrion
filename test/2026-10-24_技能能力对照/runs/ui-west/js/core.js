/*
 * Pure state helpers. No DOM here, so the same functions can be exercised
 * with Node during verification. All counts are derived from one state object.
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  root.PapertrailCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var STATUSES = ['unread', 'reading', 'read'];
  var STATUS_LABELS = { unread: '未读', reading: '在读', read: '已读' };
  var TOPICS = ['视觉与图形', '人机交互', '科研方法'];

  function text(value) {
    return value == null ? '' : String(value).toLowerCase();
  }

  function tokens(query) {
    return text(query).trim().split(/\s+/).filter(Boolean);
  }

  function haystack(paper) {
    return text(paper.title) + ' ' + text(paper.authors);
  }

  function matchesQuery(paper, query) {
    var words = tokens(query);
    if (!words.length) return true;
    var hay = haystack(paper);
    return words.every(function (word) { return hay.indexOf(word) !== -1; });
  }

  function inScope(paper, state) {
    return matchesQuery(paper, state.query) &&
      (state.topic === 'all' || paper.topic === state.topic);
  }

  function matches(paper, state) {
    return inScope(paper, state) &&
      (state.status === 'all' || paper.status === state.status);
  }

  function filterPapers(papers, state) {
    return papers.filter(function (paper) { return matches(paper, state); });
  }

  /*
   * Facet counts ignore the status filter but respect search + topic, so the
   * number on each status tab is exactly what that tab would show.
   */
  function statusCounts(papers, state) {
    var scope = papers.filter(function (paper) { return inScope(paper, state); });
    var counts = { all: scope.length };
    STATUSES.forEach(function (status) {
      counts[status] = scope.filter(function (paper) { return paper.status === status; }).length;
    });
    return counts;
  }

  /*
   * Topic counts ignore the topic filter but respect search + status, so the
   * number on each topic is exactly what that topic would show.
   */
  function topicCounts(papers, state) {
    var scope = papers.filter(function (paper) {
      return matchesQuery(paper, state.query) &&
        (state.status === 'all' || paper.status === state.status);
    });
    var counts = { all: scope.length };
    TOPICS.forEach(function (topic) {
      counts[topic] = scope.filter(function (paper) { return paper.topic === topic; }).length;
    });
    return counts;
  }

  var collator = new Intl.Collator('zh-Hans-CN', { numeric: true, sensitivity: 'base' });

  function sortPapers(papers, sort) {
    var copy = papers.slice();
    var byTitle = function (a, b) {
      return collator.compare(a.title, b.title) || a.id.localeCompare(b.id);
    };
    if (sort === 'year-asc') {
      copy.sort(function (a, b) { return (a.year - b.year) || byTitle(a, b); });
    } else if (sort === 'title') {
      copy.sort(byTitle);
    } else {
      copy.sort(function (a, b) { return (b.year - a.year) || byTitle(a, b); });
    }
    return copy;
  }

  function nextStatusForToggle(paper) {
    return paper.status === 'read' ? 'unread' : 'read';
  }

  /* The one paper the "keep reading" panel suggests: in progress, else unread. */
  function continuePaper(papers) {
    return papers.find(function (paper) { return paper.status === 'reading'; }) ||
      papers.find(function (paper) { return paper.status === 'unread'; }) ||
      papers[0] || null;
  }

  /* Progress rail scope: search + topic, deliberately independent of status. */
  function progress(papers, state) {
    var scope = papers.filter(function (paper) { return inScope(paper, state); });
    var counts = { unread: 0, reading: 0, read: 0 };
    scope.forEach(function (paper) { counts[paper.status] += 1; });
    var total = scope.length;
    return {
      total: total,
      unread: counts.unread,
      reading: counts.reading,
      read: counts.read,
      readPercent: total ? Math.round((counts.read / total) * 100) : 0
    };
  }

  return {
    STATUSES: STATUSES,
    STATUS_LABELS: STATUS_LABELS,
    TOPICS: TOPICS,
    tokens: tokens,
    matchesQuery: matchesQuery,
    matches: matches,
    filterPapers: filterPapers,
    statusCounts: statusCounts,
    topicCounts: topicCounts,
    sortPapers: sortPapers,
    nextStatusForToggle: nextStatusForToggle,
    continuePaper: continuePaper,
    progress: progress
  };
});
