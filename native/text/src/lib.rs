//! CPython's immutable Unicode views enter the safe kernels through this adapter.
#![deny(unsafe_op_in_unsafe_fn)]

mod core;

use std::ffi::{c_int, c_void};
use std::panic::catch_unwind;
use std::slice;

/// C-compatible view of CPython's 1-, 2-, or 4-byte Unicode storage.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct UnstdText {
    data: *const c_void,
    length: usize,
    kind: c_int,
}

impl UnstdText {
    /// The C caller strongly holds the exact string and its GIL for the entire
    /// call. CPython supplies aligned, initialized, immutable storage of `length`
    /// code points whose total byte size fits `isize`, including lone surrogates.
    /// These slices never escape the exported call or enter detached threads.
    unsafe fn view<'a>(self) -> core::Text<'a> {
        if self.length == 0 {
            return core::Text::U8(&[]);
        }
        match self.kind {
            // SAFETY: The CPython shim guarantees the storage contract above.
            1 => core::Text::U8(unsafe {
                slice::from_raw_parts(self.data.cast::<u8>(), self.length)
            }),
            // SAFETY: Kind 2 denotes aligned native-endian Py_UCS2 storage.
            2 => core::Text::U16(unsafe {
                slice::from_raw_parts(self.data.cast::<u16>(), self.length)
            }),
            // SAFETY: Kind 4 denotes aligned native-endian Py_UCS4 storage.
            4 => core::Text::U32(unsafe {
                slice::from_raw_parts(self.data.cast::<u32>(), self.length)
            }),
            _ => panic!("invalid CPython Unicode storage kind"),
        }
    }
}

#[repr(C)]
pub struct UnstdTextDistanceResult {
    status: c_int,
    value: usize,
}

#[repr(C)]
pub struct UnstdTextJaroResult {
    status: c_int,
    value: f64,
}

/// Status: 1 = success, 0 = defer, -1 = allocation failure, -2 = caught panic.
fn outcome<T: Default>(
    operation: impl FnOnce() -> core::Outcome<T> + std::panic::UnwindSafe,
) -> (c_int, T) {
    match catch_unwind(operation) {
        Ok(Ok(Some(value))) => (1, value),
        Ok(Ok(None)) => (0, T::default()),
        Ok(Err(core::AllocationFailure)) => (-1, T::default()),
        Err(_) => (-2, T::default()),
    }
}

/// Compute bounded exact-string Levenshtein distance.
///
/// # Safety
/// Both views must refer to live exact CPython strings with the Unicode storage
/// contract described by `UnstdText::view`. The caller must retain their strong
/// references and the GIL until this function returns.
#[no_mangle]
pub unsafe extern "C" fn unstd_text_distance(
    a: UnstdText,
    b: UnstdText,
) -> UnstdTextDistanceResult {
    let (status, value) = outcome(|| {
        // SAFETY: The caller guarantees synchronous, GIL-held storage. Neither
        // borrowed view escapes this operation.
        core::distance(unsafe { a.view() }, unsafe { b.view() })
    });
    UnstdTextDistanceResult { status, value }
}

/// Compute bounded exact-string Jaro similarity.
///
/// # Safety
/// Both views must refer to live exact CPython strings with the Unicode storage
/// contract described by `UnstdText::view`. The caller must retain their strong
/// references and the GIL until this function returns.
#[no_mangle]
pub unsafe extern "C" fn unstd_text_jaro(a: UnstdText, b: UnstdText) -> UnstdTextJaroResult {
    let (status, value) = outcome(|| {
        // SAFETY: Same synchronous, GIL-held storage contract as distance.
        core::jaro(unsafe { a.view() }, unsafe { b.view() })
    });
    UnstdTextJaroResult { status, value }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn failures_are_contained_before_the_c_boundary() {
        assert_eq!(outcome::<usize>(|| Ok(None)), (0, 0));
        assert_eq!(outcome::<usize>(|| Err(core::AllocationFailure)), (-1, 0));
        assert_eq!(outcome::<usize>(|| panic!("contained test panic")), (-2, 0));
    }

    #[test]
    fn adapter_preserves_each_unicode_storage_kind() {
        let narrow = *b"abc";
        let wide = [u16::from(b'a'), 0xd800, u16::from(b'c')];
        let widest = [u32::from(b'a'), 0x1f600, u32::from(b'c')];
        let a = UnstdText {
            data: narrow.as_ptr().cast(),
            length: narrow.len(),
            kind: 1,
        };
        let b = UnstdText {
            data: wide.as_ptr().cast(),
            length: wide.len(),
            kind: 2,
        };
        let c = UnstdText {
            data: widest.as_ptr().cast(),
            length: widest.len(),
            kind: 4,
        };
        // SAFETY: Local immutable arrays provide aligned storage throughout.
        unsafe {
            let ab = unstd_text_distance(a, b);
            let bc = unstd_text_distance(b, c);
            assert_eq!((ab.status, ab.value), (1, 1));
            assert_eq!((bc.status, bc.value), (1, 1));
            assert_eq!(unstd_text_jaro(a, a).value, 1.0);
        }
    }
}
