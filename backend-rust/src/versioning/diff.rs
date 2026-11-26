use std::{collections::HashMap, ops::Range};

use serde::Serialize;

const MAX_PATCH_LINES: usize = 600;

#[derive(Serialize)]
pub(super) struct Line<'a> {
    #[serde(rename = "type")]
    kind: &'static str,
    content: &'a str,
}

#[derive(Default)]
pub(super) struct Changes<'a> {
    pub insertions: usize,
    pub deletions: usize,
    pub patch: Vec<Line<'a>>,
}

impl Changes<'_> {
    pub fn truncated(&self) -> bool {
        self.insertions + self.deletions > self.patch.len()
    }
}

pub(super) fn compare<'a>(before: &'a str, after: &'a str, include_patch: bool) -> Changes<'a> {
    if before == after {
        return Changes::default();
    }
    let before = lines(before);
    let after = lines(after);
    let matcher = Matcher::new(&before, &after);
    let mut changes = Changes::default();
    let mut previous = Match::default();
    for next in matcher.blocks().into_iter().chain([Match {
        before: before.len(),
        after: after.len(),
        len: 0,
    }]) {
        let removed = &before[previous.before + previous.len..next.before];
        let added = &after[previous.after + previous.len..next.after];
        changes.deletions += removed.len();
        changes.insertions += added.len();
        if include_patch {
            changes.patch.extend(
                removed
                    .iter()
                    .map(|content| Line {
                        kind: "remove",
                        content,
                    })
                    .chain(added.iter().map(|content| Line {
                        kind: "add",
                        content,
                    }))
                    .take(MAX_PATCH_LINES - changes.patch.len()),
            );
        }
        previous = next;
    }
    changes
}

fn lines(text: &str) -> Vec<&str> {
    // Match Python str.splitlines(), including CRLF and Unicode line boundaries.
    let mut result = Vec::new();
    let mut chars = text.char_indices().peekable();
    let mut start = 0;
    while let Some((index, character)) = chars.next() {
        if matches!(
            character,
            '\n' | '\r' | '\x0b' | '\x0c' | '\x1c'..='\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}'
        ) {
            result.push(&text[start..index]);
            start = index + character.len_utf8();
            if character == '\r' && chars.next_if(|(_, next)| *next == '\n').is_some() {
                start += 1;
            }
        }
    }
    if start < text.len() {
        result.push(&text[start..]);
    }
    result
}

#[derive(Default)]
struct Match {
    before: usize,
    after: usize,
    len: usize,
}

struct Matcher<'a> {
    before: &'a [&'a str],
    after: &'a [&'a str],
    positions: HashMap<&'a str, Vec<usize>>,
}

impl<'a> Matcher<'a> {
    fn new(before: &'a [&str], after: &'a [&'a str]) -> Self {
        let mut positions = HashMap::<_, Vec<_>>::new();
        for (index, &line) in after.iter().enumerate() {
            positions.entry(line).or_default().push(index);
        }
        // SequenceMatcher excludes popular lines from the initial search on long
        // files, then includes them when extending an adjacent matching block.
        if after.len() >= 200 {
            positions.retain(|_, positions| positions.len() <= after.len() / 100 + 1);
        }
        Self {
            before,
            after,
            positions,
        }
    }

    fn blocks(&self) -> Vec<Match> {
        let mut search = vec![(0..self.before.len(), 0..self.after.len())];
        let mut matches = Vec::new();
        while let Some((before, after)) = search.pop() {
            let longest = self.longest(&before, &after);
            if longest.len == 0 {
                continue;
            }
            if before.start < longest.before && after.start < longest.after {
                search.push((before.start..longest.before, after.start..longest.after));
            }
            let end_before = longest.before + longest.len;
            let end_after = longest.after + longest.len;
            if end_before < before.end && end_after < after.end {
                search.push((end_before..before.end, end_after..after.end));
            }
            matches.push(longest);
        }
        matches.sort_unstable_by_key(|part| (part.before, part.after));
        matches
    }

    fn longest(&self, before: &Range<usize>, after: &Range<usize>) -> Match {
        let mut best = Match {
            before: before.start,
            after: after.start,
            len: 0,
        };
        let mut previous = HashMap::<usize, usize>::new();
        let mut current = HashMap::new();
        for i in before.clone() {
            if let Some(positions) = self.positions.get(self.before[i]) {
                let first = positions.partition_point(|&j| j < after.start);
                for &j in positions[first..].iter().take_while(|&&j| j < after.end) {
                    let len = previous.get(&j.wrapping_sub(1)).copied().unwrap_or(0) + 1;
                    current.insert(j, len);
                    if len > best.len {
                        best = Match {
                            before: i + 1 - len,
                            after: j + 1 - len,
                            len,
                        };
                    }
                }
            }
            std::mem::swap(&mut current, &mut previous);
            current.clear();
        }
        while best.before > before.start
            && best.after > after.start
            && self.before[best.before - 1] == self.after[best.after - 1]
        {
            best.before -= 1;
            best.after -= 1;
            best.len += 1;
        }
        while best.before + best.len < before.end
            && best.after + best.len < after.end
            && self.before[best.before + best.len] == self.after[best.after + best.len]
        {
            best.len += 1;
        }
        best
    }
}
