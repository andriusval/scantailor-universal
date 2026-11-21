/*
    Scan Tailor - Interactive post-processing tool for scanned pages.
    Copyright (C) 2026  ScanTailor Universal contributors

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/


#ifndef OUTPUT_GRAYSCALE_DETECTOR_H_
#define OUTPUT_GRAYSCALE_DETECTOR_H_

class QImage;
class QRectF;

namespace output
{

// Finds gray pictures in color scans, for example halftones on yellow paper, so that "Convert to grayscale first" can work automatically.
class GrayscaleDetector
{
public:
    // A page with a chroma value below this limit is gray.
    static double chromaThreshold()
    {
        return 10.0;
    }

    // Returns the 95th percentile of the chroma of the mid-tone pixels in the area, after a limited correction for the paper tint.
    static double measureChroma(QImage const& image, QRectF const& area);

    static bool isGrayscale(double chroma)
    {
        return chroma < chromaThreshold();
    }
};

} // namespace output

#endif
