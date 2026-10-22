//! Bounded code-point kernels, independent of Python and raw pointers.
#![forbid(unsafe_code)]

use std::mem::size_of;

const MAX_PATTERN: usize = 65_536;
const MAX_TEXT: usize = 262_144;
const MAX_WORD_STEPS: usize = 4_194_304;
const MAX_JARO_LENGTH: usize = 65_536;
const MAX_JARO_PAIRS: usize = 4_194_304;
const MAX_ALPHABET: usize = 256;
const HASH_SLOTS: usize = 512;
const EMPTY_CHAR: u32 = u32::MAX;

#[derive(Clone, Copy)]
pub(crate) enum Text<'a> {
    U8(&'a [u8]),
    U16(&'a [u16]),
    U32(&'a [u32]),
}

#[derive(Debug)]
pub(crate) struct AllocationFailure;
pub(crate) type Outcome<T> = Result<Option<T>, AllocationFailure>;

trait CodePoint: Copy {
    fn code_point(self) -> u32;
}
impl CodePoint for u8 {
    #[inline(always)]
    fn code_point(self) -> u32 {
        u32::from(self)
    }
}
impl CodePoint for u16 {
    #[inline(always)]
    fn code_point(self) -> u32 {
        u32::from(self)
    }
}
impl CodePoint for u32 {
    #[inline(always)]
    fn code_point(self) -> u32 {
        self
    }
}

// Dispatch once, so numerical loops use typed slices without a kind check per
// character. u32 deliberately includes Python's lone surrogate code points.
macro_rules! dispatch {
    ($a:expr, $b:expr, $kernel:ident) => {
        match ($a, $b) {
            (Text::U8(a), Text::U8(b)) => $kernel(a, b),
            (Text::U8(a), Text::U16(b)) => $kernel(a, b),
            (Text::U8(a), Text::U32(b)) => $kernel(a, b),
            (Text::U16(a), Text::U8(b)) => $kernel(a, b),
            (Text::U16(a), Text::U16(b)) => $kernel(a, b),
            (Text::U16(a), Text::U32(b)) => $kernel(a, b),
            (Text::U32(a), Text::U8(b)) => $kernel(a, b),
            (Text::U32(a), Text::U16(b)) => $kernel(a, b),
            (Text::U32(a), Text::U32(b)) => $kernel(a, b),
        }
    };
}

pub(crate) fn distance(a: Text<'_>, b: Text<'_>) -> Outcome<usize> {
    dispatch!(a, b, distance_for)
}
pub(crate) fn jaro(a: Text<'_>, b: Text<'_>) -> Outcome<f64> {
    dispatch!(a, b, jaro_for)
}

#[inline]
fn character_slot(value: u32, keys: &[u32; HASH_SLOTS]) -> usize {
    let mut slot = value.wrapping_mul(2_654_435_761) as usize & (HASH_SLOTS - 1);
    while keys[slot] != EMPTY_CHAR && keys[slot] != value {
        slot = (slot + 1) & (HASH_SLOTS - 1);
    }
    slot
}

fn zeroed<T: Default + Clone>(count: usize) -> Result<Vec<T>, AllocationFailure> {
    let mut values = Vec::new();
    values
        .try_reserve_exact(count)
        .map_err(|_| AllocationFailure)?;
    values.resize(count, T::default());
    Ok(values)
}

fn distance_for<A: CodePoint, B: CodePoint>(a: &[A], b: &[B]) -> Outcome<usize> {
    if a.len() > b.len() {
        distance_ordered(b, a)
    } else {
        distance_ordered(a, b)
    }
}

fn distance_ordered<A: CodePoint, B: CodePoint>(pattern: &[A], text: &[B]) -> Outcome<usize> {
    if pattern.is_empty() {
        return Ok(Some(text.len()));
    }
    if pattern.len() > MAX_PATTERN || text.len() > MAX_TEXT {
        return Ok(None);
    }
    let words = pattern.len().div_ceil(64);
    if text.len() > MAX_WORD_STEPS / words {
        return Ok(None);
    }

    let mut keys = [EMPTY_CHAR; HASH_SLOTS];
    let mut slots = [0_u16; HASH_SLOTS];
    let mut alphabet = 0;
    for &character in pattern {
        let value = character.code_point();
        let slot = character_slot(value, &keys);
        if keys[slot] == EMPTY_CHAR {
            if alphabet == MAX_ALPHABET {
                return Ok(None);
            }
            keys[slot] = value;
            slots[slot] = alphabet as u16;
            alphabet += 1;
        }
    }
    if words == 1 {
        return Ok(Some(distance_single(pattern, text, &keys, &slots)));
    }

    // The admitted maximum is 261 * 1024 u64s. Keep a checked byte-size bound
    // so later admission-limit changes cannot invalidate the allocation proof.
    let count = (alphabet + 5)
        .checked_mul(words)
        .filter(|&count| count <= isize::MAX as usize / size_of::<u64>())
        .ok_or(AllocationFailure)?;
    let mut storage = zeroed::<u64>(count)?;
    for (index, &character) in pattern.iter().enumerate() {
        let slot = character_slot(character.code_point(), &keys);
        storage[usize::from(slots[slot]) * words + index / 64] |= 1_u64 << (index % 64);
    }
    let (masks, states) = storage.split_at_mut(alphabet * words);
    let (positive, states) = states.split_at_mut(words);
    let (negative, states) = states.split_at_mut(words);
    let (differences, states) = states.split_at_mut(words);
    let (horizontal_positive, horizontal_negative) = states.split_at_mut(words);
    positive.fill(u64::MAX);
    let high_bit = 1_u64 << ((pattern.len() - 1) % 64);
    let final_mask = final_mask(pattern.len());
    positive[words - 1] &= final_mask;
    let mut score = pattern.len();
    for &character in text {
        let slot = character_slot(character.code_point(), &keys);
        let matches = if keys[slot] == EMPTY_CHAR {
            None
        } else {
            let start = usize::from(slots[slot]) * words;
            Some(&masks[start..start + words])
        };
        let mut carry = 0;
        for word in 0..words {
            let equal = matches.map_or(0, |masks| masks[word]);
            let addend = equal & positive[word];
            let (sum, overflow) = addend.overflowing_add(positive[word]);
            let (with_carry, carried_overflow) = sum.overflowing_add(carry);
            carry = u64::from(overflow | carried_overflow);
            let changed = (with_carry ^ positive[word]) | equal | negative[word];
            differences[word] = changed;
            horizontal_positive[word] = negative[word] | !(changed | positive[word]);
            horizontal_negative[word] = changed & positive[word];
        }
        score += usize::from(horizontal_positive[words - 1] & high_bit != 0);
        score -= usize::from(horizontal_negative[words - 1] & high_bit != 0);
        let mut carry_positive = 1;
        let mut carry_negative = 0;
        for word in 0..words {
            let hp = horizontal_positive[word];
            let hn = horizontal_negative[word];
            let shifted_positive = (hp << 1) | carry_positive;
            let shifted_negative = (hn << 1) | carry_negative;
            carry_positive = hp >> 63;
            carry_negative = hn >> 63;
            positive[word] = shifted_negative | !(differences[word] | shifted_positive);
            negative[word] = differences[word] & shifted_positive;
        }
        positive[words - 1] &= final_mask;
        negative[words - 1] &= final_mask;
    }
    Ok(Some(score))
}

#[inline]
fn final_mask(length: usize) -> u64 {
    let remainder = length % 64;
    if remainder == 0 {
        u64::MAX
    } else {
        (1_u64 << remainder) - 1
    }
}

fn distance_single<A: CodePoint, B: CodePoint>(
    pattern: &[A],
    text: &[B],
    keys: &[u32; HASH_SLOTS],
    slots: &[u16; HASH_SLOTS],
) -> usize {
    // Short strings stay on the stack, with recurrence state kept in registers.
    let mut masks = [0_u64; MAX_ALPHABET];
    for (index, &character) in pattern.iter().enumerate() {
        let slot = character_slot(character.code_point(), keys);
        masks[usize::from(slots[slot])] |= 1_u64 << index;
    }
    let final_mask = final_mask(pattern.len());
    let high_bit = 1_u64 << (pattern.len() - 1);
    let mut positive = final_mask;
    let mut negative = 0;
    let mut score = pattern.len();
    for &character in text {
        let slot = character_slot(character.code_point(), keys);
        let equal = if keys[slot] == EMPTY_CHAR {
            0
        } else {
            masks[usize::from(slots[slot])]
        };
        let changed = ((equal & positive).wrapping_add(positive) ^ positive) | equal | negative;
        let horizontal_positive = negative | !(changed | positive);
        let horizontal_negative = changed & positive;
        score += usize::from(horizontal_positive & high_bit != 0);
        score -= usize::from(horizontal_negative & high_bit != 0);
        let shifted_positive = (horizontal_positive << 1) | 1;
        positive = ((horizontal_negative << 1) | !(changed | shifted_positive)) & final_mask;
        negative = changed & shifted_positive & final_mask;
    }
    score
}

fn jaro_for<A: CodePoint, B: CodePoint>(a: &[A], b: &[B]) -> Outcome<f64> {
    if a.len() > MAX_JARO_LENGTH || b.len() > MAX_JARO_LENGTH {
        return Ok(None);
    }
    if a.is_empty() || b.is_empty() {
        return Ok(Some(if a.len() == b.len() { 1.0 } else { 0.0 }));
    }
    if a.len() > MAX_JARO_PAIRS / b.len() {
        return Ok(None);
    }
    let mut taken = zeroed::<u8>(b.len())?;
    let mut matched = Vec::<u32>::new();
    matched
        .try_reserve_exact(a.len().min(b.len()))
        .map_err(|_| AllocationFailure)?;
    let window = (a.len().max(b.len()) / 2).saturating_sub(1);
    for (index, &character) in a.iter().enumerate() {
        let value = character.code_point();
        let start = index.saturating_sub(window);
        let stop = (index + window + 1).min(b.len());
        if start >= stop {
            continue;
        }
        for (used, &candidate) in taken[start..stop].iter_mut().zip(&b[start..stop]) {
            if *used == 0 && candidate.code_point() == value {
                *used = 1;
                matched.push(value);
                break;
            }
        }
    }
    let matches = matched.len();
    if matches == 0 {
        return Ok(Some(0.0));
    }
    let mut mismatch = 0;
    let mut index = 0;
    for (&character, &used) in b.iter().zip(&taken) {
        if used != 0 {
            mismatch += usize::from(character.code_point() != matched[index]);
            index += 1;
        }
    }
    // Match Python's separate division/addition operations and integer floor.
    // Do not enable fast-math, reassociation, or floating-point contraction.
    let score = (matches as f64 / a.len() as f64
        + matches as f64 / b.len() as f64
        + (matches - mismatch / 2) as f64 / matches as f64)
        / 3.0;
    Ok(Some(score))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reference_distance(a: &[u32], b: &[u32]) -> usize {
        let mut previous: Vec<_> = (0..=b.len()).collect();
        for (i, left) in a.iter().enumerate() {
            let mut current = vec![i + 1];
            for (j, right) in b.iter().enumerate() {
                current.push(
                    (previous[j + 1] + 1)
                        .min(current[j] + 1)
                        .min(previous[j] + usize::from(left != right)),
                );
            }
            previous = current;
        }
        previous[b.len()]
    }

    fn reference_jaro(a: &[u32], b: &[u32]) -> f64 {
        if a == b {
            return 1.0;
        }
        if a.is_empty() || b.is_empty() {
            return 0.0;
        }
        let window = (a.len().max(b.len()) / 2).saturating_sub(1);
        let mut taken = vec![false; b.len()];
        let mut a_matched = Vec::new();
        for (i, &left) in a.iter().enumerate() {
            for j in i.saturating_sub(window)..(i + window + 1).min(b.len()) {
                if !taken[j] && b[j] == left {
                    taken[j] = true;
                    a_matched.push(left);
                    break;
                }
            }
        }
        let b_matched: Vec<_> = b
            .iter()
            .zip(&taken)
            .filter_map(|(&v, &used)| used.then_some(v))
            .collect();
        let count = a_matched.len();
        if count == 0 {
            return 0.0;
        }
        let swaps = a_matched
            .iter()
            .zip(&b_matched)
            .filter(|(left, right)| left != right)
            .count()
            / 2;
        (count as f64 / a.len() as f64
            + count as f64 / b.len() as f64
            + (count - swaps) as f64 / count as f64)
            / 3.0
    }

    #[test]
    fn exhaustive_short_strings_match_independent_oracles() {
        let mut words = vec![Vec::new()];
        for length in 1..=5 {
            for bits in 0..1_usize << length {
                words.push(
                    (0..length)
                        .map(|bit| 97 + ((bits >> bit) & 1) as u32)
                        .collect(),
                );
            }
        }
        for a in &words {
            for b in &words {
                assert_eq!(
                    distance(Text::U32(a), Text::U32(b)).unwrap(),
                    Some(reference_distance(a, b))
                );
                assert_eq!(
                    jaro(Text::U32(a), Text::U32(b)).unwrap(),
                    Some(reference_jaro(a, b))
                );
            }
        }
    }

    #[test]
    fn carries_and_partial_words_preserve_unicode_code_points() {
        for width in [1, 2, 31, 63, 64, 65, 127, 128, 129, 511, 1024] {
            let a: Vec<_> = [97, 0, 0xd800, 0x1f600]
                .into_iter()
                .cycle()
                .take(width)
                .collect();
            let mut changed = a.clone();
            changed[width - 1] = 98;
            let mut shifted = vec![98];
            shifted.extend_from_slice(&a);
            for b in [changed, shifted, vec![120; width]] {
                let expected = reference_distance(&a, &b);
                assert_eq!(
                    distance(Text::U32(&a), Text::U32(&b)).unwrap(),
                    Some(expected)
                );
                assert_eq!(
                    distance(Text::U32(&b), Text::U32(&a)).unwrap(),
                    Some(expected)
                );
            }
        }
    }

    #[test]
    fn storage_widths_and_surrogates_are_not_utf8_transcoded() {
        let a = [97_u8, 0, 233, 98];
        let a16: Vec<_> = a.iter().map(|&v| u16::from(v)).collect();
        let a32: Vec<_> = a.iter().map(|&v| u32::from(v)).collect();
        let b16 = [97_u16, 0xd800, 233, 98];
        let b32: Vec<_> = b16.iter().map(|&v| u32::from(v)).collect();
        for left in [Text::U8(&a), Text::U16(&a16), Text::U32(&a32)] {
            for right in [Text::U16(&b16), Text::U32(&b32)] {
                assert_eq!(distance(left, right).unwrap(), Some(1));
                assert_eq!(distance(right, left).unwrap(), Some(1));
                assert_eq!(jaro(left, right).unwrap(), Some(reference_jaro(&a32, &b32)));
                assert_eq!(jaro(right, left).unwrap(), Some(reference_jaro(&b32, &a32)));
            }
        }
    }

    #[test]
    fn colliding_full_alphabet_retains_distinct_masks() {
        let alphabet: Vec<_> = (0..256).map(|i| 0x1000 + 512 * i).collect();
        let a: Vec<_> = alphabet.into_iter().cycle().take(1024).collect();
        let mut b = a.clone();
        b.rotate_left(17);
        assert_eq!(
            distance(Text::U32(&a), Text::U32(&b)).unwrap(),
            Some(reference_distance(&a, &b))
        );
    }

    #[test]
    fn admission_limits_match_existing_native_contract() {
        assert_eq!(
            distance(Text::U8(&[]), Text::U8(&vec![0; MAX_TEXT + 1])).unwrap(),
            Some(MAX_TEXT + 1)
        );
        assert!(distance(
            Text::U8(&vec![0; MAX_PATTERN + 1]),
            Text::U8(&vec![1; MAX_PATTERN + 1])
        )
        .unwrap()
        .is_none());
        assert!(distance(Text::U8(&[0]), Text::U8(&vec![1; MAX_TEXT + 1]))
            .unwrap()
            .is_none());
        assert_eq!(
            distance(Text::U8(&vec![0; 16_384]), Text::U8(&vec![1; 16_384])).unwrap(),
            Some(16_384)
        );
        assert!(
            distance(Text::U8(&vec![0; 16_384]), Text::U8(&vec![1; 16_385]))
                .unwrap()
                .is_none()
        );
        assert!(distance(
            Text::U32(&(0..257).collect::<Vec<_>>()),
            Text::U8(&vec![0; 258])
        )
        .unwrap()
        .is_none());
        assert_eq!(
            jaro(Text::U8(&vec![0; 2048]), Text::U8(&vec![0; 2048])).unwrap(),
            Some(1.0)
        );
        assert!(jaro(Text::U8(&vec![0; 2048]), Text::U8(&vec![0; 2049]))
            .unwrap()
            .is_none());
        assert!(jaro(Text::U8(&[]), Text::U8(&vec![0; MAX_JARO_LENGTH + 1]))
            .unwrap()
            .is_none());
    }

    #[test]
    fn jaro_keeps_odd_mismatch_floor_and_exact_float_operations() {
        let a: Vec<_> = "gecaedbc".chars().map(u32::from).collect();
        let b: Vec<_> = "acde".chars().map(u32::from).collect();
        assert_eq!(
            jaro(Text::U32(&a), Text::U32(&b)).unwrap(),
            Some(reference_jaro(&a, &b))
        );
        assert_eq!(
            jaro(Text::U8(b"CRATE"), Text::U8(b"TRACE")).unwrap(),
            Some(0.7333333333333334)
        );
    }
}
