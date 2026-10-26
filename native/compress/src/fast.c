/* Instantiate the authenticated upstream compressor with the owned HT finder.
 * HC/lazy levels retain the upstream implementation and parameters. */
#include "../vendor/libdeflate/lib/deflate_constants.h"
#define MATCHFINDER_WINDOW_ORDER DEFLATE_WINDOW_ORDER
#include "fast.h"
#include "../vendor/libdeflate/lib/deflate_compress.c"
