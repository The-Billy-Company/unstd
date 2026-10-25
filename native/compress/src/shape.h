#ifndef UNSTD_DEFLATE_CLASSIFY_H
#define UNSTD_DEFLATE_CLASSIFY_H

#include <stdint.h>

/* This bounded structural probe walks a complete small final block without
 * producing bytes or consuming decoder/source state. The chosen inflater still
 * consumes and validates it normally. Unsupported or incomplete probes keep
 * the default codec. No expanded output or decoder state is cached. */
typedef struct {
    const unsigned char *data;
    uint32_t size;
    uint32_t position;
    uint32_t hold;
    unsigned int bits;
} unstd_cl_reader;

static int
unstd_cl_read(unstd_cl_reader *reader, unsigned int count, unsigned int *value)
{
    while (reader->bits < count) {
        if (reader->position == reader->size) {
            return 0;
        }
        reader->hold |= (uint32_t)reader->data[reader->position++] << reader->bits;
        reader->bits += 8;
    }
    *value = reader->hold & ((1U << count) - 1U);
    reader->hold >>= count;
    reader->bits -= count;
    return 1;
}

static int
unstd_cl_complete(const unsigned char *lengths, unsigned int size,
                  unsigned int maximum)
{
    uint32_t total = 0;
    for (unsigned int i = 0; i < size; ++i) {
        unsigned int length = lengths[i];
        if (length > maximum) {
            return 0;
        }
        if (length != 0) {
            total += 1U << (maximum - length);
        }
    }
    /* At most 294 code lengths and maximum 15: this sum cannot overflow. */
    return total == (1U << maximum);
}

/* The same complete seven-bit lookup storage serves precode and distances. */
static void
unstd_cl_table7(uint16_t *table, const unsigned char *lengths, unsigned int size)
{
    unsigned int code = 0;
    for (unsigned int bits = 1; bits <= 7; ++bits) {
        for (unsigned int symbol = 0; symbol < size; ++symbol) {
            if (lengths[symbol] == bits) {
                unsigned int reversed = 0;
                unsigned int value = code++;
                for (unsigned int j = 0; j < bits; ++j) {
                    reversed = (reversed << 1) | (value & 1U);
                    value >>= 1;
                }
                for (unsigned int slot = reversed; slot < 128;
                     slot += 1U << bits) {
                    table[slot] = (uint16_t)((bits << 8) | symbol);
                }
            }
        }
        code <<= 1;
    }
}

/* Nonzero input only: GCC/Clang emit a bounded bit-scan instruction. */
static unsigned int
unstd_cl_trailing(uint32_t value)
{
#if defined(__GNUC__) || defined(__clang__)
    return (unsigned int)__builtin_ctz(value);
#else
    unsigned int count = 0;
    while ((value & 1U) == 0) {
        value >>= 1;
        ++count;
    }
    return count;
#endif
}

/* bits is 0..31 and pair_bits is 2..11, from the bounded reader and trees. */
static unsigned int
unstd_cl_groups(uint32_t hold, unsigned int bits,
                 unsigned int pair, unsigned int pair_bits)
{
    static const uint32_t spread[12] = {
        UINT32_C(0x00000000), UINT32_C(0xffffffff), UINT32_C(0x55555555), UINT32_C(0x49249249),
        UINT32_C(0x11111111), UINT32_C(0x42108421), UINT32_C(0x41041041), UINT32_C(0x10204081),
        UINT32_C(0x01010101), UINT32_C(0x08040201), UINT32_C(0x40100401), UINT32_C(0x00400801)
    };
    uint32_t valid = (UINT32_C(1) << bits) - 1U;
    /* Unsigned multiplication tiles the pair; excess high bits wrap away. */
    uint32_t difference = (hold ^ (pair * spread[pair_bits])) & valid;
    unsigned int available = bits / pair_bits;
    if (difference != 0) {
        unsigned int equal = unstd_cl_trailing(difference) / pair_bits;
        if (equal < available) {
            return equal;
        }
    }
    return available;
}

static int
unstd_cl_body(unstd_cl_reader *reader, const unsigned char *lengths,
              unsigned int distances, uint16_t *table)
{
    static const uint16_t length_base[28] = {
        3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23,
        27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227
    };
    static const unsigned char length_extra[28] = {
        0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2,
        2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5
    };
    static const unsigned char distance_base[8] = {1, 2, 3, 4, 5, 7, 9, 13};
    static const unsigned char distance_extra[8] = {0, 0, 0, 0, 1, 1, 2, 2};
    /* At most 16 literals, END, and 29 length codes. No large decode table. */
    uint16_t symbols[46];
    uint16_t first[16];
    unsigned char counts[16] = {0};
    unsigned char offsets[16];
    for (unsigned int symbol = 0; symbol < 286; ++symbol) {
        if (lengths[symbol] != 0) {
            ++counts[lengths[symbol]];
        }
    }
    unsigned int code = 0;
    unsigned int index = 0;
    for (unsigned int bits = 1; bits <= 15; ++bits) {
        first[bits] = (uint16_t)code;
        offsets[bits] = (unsigned char)index;
        for (unsigned int symbol = 0; symbol < 286; ++symbol) {
            if (lengths[symbol] == bits) {
                symbols[index++] = (uint16_t)symbol;
            }
        }
        code = (code + counts[bits]) << 1;
    }
    unsigned int long_bits = lengths[285];
    unsigned int long_code = first[long_bits] + counts[long_bits] - 1U;
    unsigned int long_wire = long_bits == 1 ? long_code
        : ((long_code & 1U) << 1) | (long_code >> 1);
    unsigned int long_mask = (1U << long_bits) - 1U;
    unstd_cl_table7(table, lengths + 286, distances);
    uint32_t produced = 0;
    uint32_t long_matches = 0;
    unsigned int long_distance = 0;
    unsigned int literals = 0;
    unsigned int other_matches = 0;
    unsigned int tokens = 0;
    for (;;) {
        while (reader->bits < long_bits && reader->position < reader->size) {
            reader->hold |= (uint32_t)reader->data[reader->position++] << reader->bits;
            reader->bits += 8;
        }
        unsigned int symbol;
        if (reader->bits >= long_bits
                && (reader->hold & long_mask) == long_wire) {
            reader->hold >>= long_bits;
            reader->bits -= long_bits;
            symbol = 285;
        }
        else {
            unsigned int prefix = 0;
            for (unsigned int bits = 1; ; ++bits) {
                unsigned int bit;
                if (bits > 15 || !unstd_cl_read(reader, 1, &bit)) {
                    return 0;
                }
                prefix = (prefix << 1) | bit;
                if (prefix >= first[bits]
                        && prefix - first[bits] < counts[bits]) {
                    symbol = symbols[offsets[bits] + prefix - first[bits]];
                    break;
                }
            }
        }
        if (symbol == 256) {
            /* A real EOB is required, not an assumed final payload boundary.
             * All values are bounded by 4096 tokens, so products fit uint32. */
            return long_matches >= 64 && long_matches * 258U * 10U >= produced * 9U;
        }
        if (++tokens > 4096) {
            return 0;
        }
        if (symbol < 256) {
            if (++literals > 32) {
                return 0;
            }
            ++produced;
            continue;
        }
        unsigned int length = 258;
        if (symbol != 285) {
            unsigned int extra;
            if (++other_matches > 8
                    || !unstd_cl_read(reader, length_extra[symbol - 257], &extra)) {
                return 0;
            }
            length = length_base[symbol - 257] + extra;
        }
        while (reader->bits < 7 && reader->position < reader->size) {
            reader->hold |= (uint32_t)reader->data[reader->position++] << reader->bits;
            reader->bits += 8;
        }
        unsigned int entry = table[reader->hold & 127U];
        unsigned int distance_bits = entry >> 8;
        if (reader->bits < distance_bits) {
            return 0;
        }
        unsigned int distance_wire = reader->hold & ((1U << distance_bits) - 1U);
        reader->hold >>= distance_bits;
        reader->bits -= distance_bits;
        unsigned int distance_symbol = entry & 255U;
        unsigned int extra;
        unsigned int extra_bits = distance_extra[distance_symbol];
        if (!unstd_cl_read(reader, extra_bits, &extra)) {
            return 0;
        }
        unsigned int distance = distance_base[distance_symbol] + extra;
        if (distance > produced) {
            return 0;
        }
        if (symbol == 285) {
            /* Alternating long distances defeat the cheap repeated-pair scan
             * and can be slower in NG. Reject a change before scanning on. */
            if (long_distance != 0 && distance != long_distance) {
                return 0;
            }
            long_distance = distance;
        }
        produced += length;
        if (symbol != 285) {
            continue;
        }
        ++long_matches;

        /* Identical long-match/distance pairs are still parsed from the wire.
         * Batch their bit checks without constructing bytes or replaying any
         * decoder state. The first validated distance remains valid as output
         * count increases. This keeps a whole 1 MiB run probe cheap. */
        unsigned int pair_bits = long_bits + distance_bits + extra_bits;
        unsigned int pair = long_wire | (distance_wire << long_bits)
            | (extra << (long_bits + distance_bits));
        while (reader->bits < 24 && reader->position < reader->size) {
            reader->hold |= (uint32_t)reader->data[reader->position++] << reader->bits;
            reader->bits += 8;
        }
        unsigned int groups = unstd_cl_groups(reader->hold, reader->bits,
                                               pair, pair_bits);
        if (groups > 4096U - tokens) {
            return 0;
        }
        unsigned int advance = groups * pair_bits;  /* At most 31, never 32. */
        reader->hold >>= advance;
        reader->bits -= advance;
        tokens += groups;
        long_matches += groups;
        produced += groups * 258U;
    }
}

#if defined(_MSC_VER)
#define UNSTD_CL_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define UNSTD_CL_NOINLINE __attribute__((noinline))
#else
#define UNSTD_CL_NOINLINE
#endif

/* Keep the large bounded frame off the ordinary two-byte rejection path. */
static int UNSTD_CL_NOINLINE
unstd_cl_dynamic(const unsigned char *data, uint32_t size)
{
    static const unsigned char order[19] = {
        16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15
    };
    unsigned char lengths[294];
    uint16_t table[128];
    unsigned int distances = (data[1] & 31U) + 1U;
    unstd_cl_reader reader = {data, size, 2, data[1] >> 5, 3};
    unsigned int encoded;
    if (!unstd_cl_read(&reader, 4, &encoded)) {
        return 0;
    }
    unsigned int precodes = encoded + 4U;
    for (unsigned int i = 0; i < 19; ++i) {
        lengths[i] = 0;
    }
    for (unsigned int i = 0; i < precodes; ++i) {
        if (!unstd_cl_read(&reader, 3, &encoded)) {
            return 0;
        }
        lengths[order[i]] = (unsigned char)encoded;
    }
    if (!unstd_cl_complete(lengths, 19, 7)) {
        return 0;
    }

    unstd_cl_table7(table, lengths, 19);

    unsigned int size_needed = 286U + distances;
    unsigned int written = 0;
    while (written < size_needed) {
        /* Partial lookahead is enough when the selected precode is shorter
         * than seven bits; never require or read bytes beyond this buffer. */
        while (reader.bits < 7 && reader.position < reader.size) {
            reader.hold |= (uint32_t)reader.data[reader.position++] << reader.bits;
            reader.bits += 8;
        }
        unsigned int entry = table[reader.hold & 127U];
        unsigned int bits = entry >> 8;
        if (reader.bits < bits) {
            return 0;
        }
        reader.hold >>= bits;
        reader.bits -= bits;
        unsigned int symbol = entry & 255U;
        if (symbol < 16) {
            lengths[written++] = (unsigned char)symbol;
            continue;
        }
        unsigned int count;
        unsigned char repeated;
        if (symbol == 16) {
            if (written == 0 || !unstd_cl_read(&reader, 2, &encoded)) {
                return 0;
            }
            repeated = lengths[written - 1];
            count = encoded + 3U;
        }
        else if (symbol == 17) {
            if (!unstd_cl_read(&reader, 3, &encoded)) {
                return 0;
            }
            repeated = 0;
            count = encoded + 3U;
        }
        else {
            if (!unstd_cl_read(&reader, 7, &encoded)) {
                return 0;
            }
            repeated = 0;
            count = encoded + 11U;
        }
        if (count > size_needed - written) {
            return 0;
        }
        do {
            lengths[written++] = repeated;
        } while (--count != 0);
    }

    if (lengths[256] == 0 || lengths[285] == 0 || lengths[285] > 2
            || !unstd_cl_complete(lengths, 286, 15)
            || !unstd_cl_complete(lengths + 286, distances, 7)) {
        return 0;
    }
    unsigned int literals = 0;
    for (unsigned int i = 0; i < 256; ++i) {
        literals += lengths[i] != 0;
    }
    return literals <= 16 && unstd_cl_body(&reader, lengths, distances, table);
}

static int
unstd_prefer_ng(const unsigned char *data, uint32_t size)
{
    /* BFINAL=1, BTYPE=dynamic, HLIT=29 (286 literal/length codes).
     * Eight distance codes cover distances 1..16. Ordinary large-window JSON
     * rejects before any stack-array setup or Huffman work. */
    if (size < 2 || size > 8192 || data[0] != 0xed || (data[1] & 31U) >= 8U) {
        return 0;
    }
    return unstd_cl_dynamic(data, size);
}
#undef UNSTD_CL_NOINLINE
#endif
