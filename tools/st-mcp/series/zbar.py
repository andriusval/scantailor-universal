# A small ctypes layer over the system libzbar lets the scripts decode barcodes without extra Python packages.
from __future__ import annotations

import ctypes
import ctypes.util

import numpy as np

_lib = ctypes.CDLL(ctypes.util.find_library("zbar") or "libzbar.so.0")
_lib.zbar_image_scanner_create.restype = ctypes.c_void_p
_lib.zbar_image_create.restype = ctypes.c_void_p
_lib.zbar_image_first_symbol.restype = ctypes.c_void_p
_lib.zbar_image_first_symbol.argtypes = [ctypes.c_void_p]
_lib.zbar_symbol_next.restype = ctypes.c_void_p
_lib.zbar_symbol_next.argtypes = [ctypes.c_void_p]
_lib.zbar_symbol_get_data.restype = ctypes.c_char_p
_lib.zbar_symbol_get_data.argtypes = [ctypes.c_void_p]
_lib.zbar_symbol_get_type.argtypes = [ctypes.c_void_p]
_lib.zbar_get_symbol_name.restype = ctypes.c_char_p
for name in ("zbar_symbol_get_loc_size", "zbar_symbol_get_loc_x", "zbar_symbol_get_loc_y"):
    getattr(_lib, name).argtypes = [ctypes.c_void_p] + ([ctypes.c_uint] if name != "zbar_symbol_get_loc_size" else [])
_lib.zbar_image_scanner_set_config.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
_lib.zbar_image_set_format.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_lib.zbar_image_set_size.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
_lib.zbar_image_set_data.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
_lib.zbar_scan_image.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_lib.zbar_image_destroy.argtypes = [ctypes.c_void_p]
_lib.zbar_image_scanner_destroy.argtypes = [ctypes.c_void_p]

_Y800 = ord("Y") | ord("8") << 8 | ord("0") << 16 | ord("0") << 24


def decode(gray: np.ndarray) -> list[dict]:
    a = np.ascontiguousarray(gray, dtype=np.uint8)
    h, w = a.shape
    scanner = _lib.zbar_image_scanner_create()
    # Symbology 0 and config 0 enable all barcode types.
    _lib.zbar_image_scanner_set_config(scanner, 0, 0, 1)
    img = _lib.zbar_image_create()
    _lib.zbar_image_set_format(img, _Y800)
    _lib.zbar_image_set_size(img, w, h)
    _lib.zbar_image_set_data(img, a.ctypes.data, a.size, None)
    out = []
    try:
        _lib.zbar_scan_image(scanner, img)
        sym = _lib.zbar_image_first_symbol(img)
        while sym:
            n = _lib.zbar_symbol_get_loc_size(sym)
            xs = [_lib.zbar_symbol_get_loc_x(sym, i) for i in range(n)]
            ys = [_lib.zbar_symbol_get_loc_y(sym, i) for i in range(n)]
            out.append({"type": _lib.zbar_get_symbol_name(_lib.zbar_symbol_get_type(sym)).decode(),
                        "data": _lib.zbar_symbol_get_data(sym).decode(errors="replace"),
                        "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys)})
            sym = _lib.zbar_symbol_next(sym)
    finally:
        # The data belongs to numpy, so the image must not free it; zbar_image_destroy only drops the header here.
        _lib.zbar_image_set_data(img, None, 0, None)
        _lib.zbar_image_destroy(img)
        _lib.zbar_image_scanner_destroy(scanner)
    return out
