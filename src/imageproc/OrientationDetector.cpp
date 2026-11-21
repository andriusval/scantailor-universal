/*
    Scan Tailor - Interactive post-processing tool for scanned pages.

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

#include "OrientationDetector.h"
#include "BinaryImage.h"
#include "BinaryThreshold.h"
#include "ConnComp.h"
#include "ConnCompEraser.h"
#include "Connectivity.h"
#include "GrayImage.h"
#include "Scale.h"
#include <QImage>
#include <QImageIOHandler>
#include <QImageReader>
#include <QSize>
#include <QString>
#include <algorithm>
#include <cmath>
#include <vector>

namespace imageproc
{

namespace
{

struct Box {
    int x0;
    int y0;
    int x1;
    int y1;

    int width() const
    {
        return x1 - x0;
    }

    int height() const
    {
        return y1 - y0;
    }

    int maxSide() const
    {
        return std::max(width(), height());
    }
};

// One cue collects the votes for upright text and for upside-down text.
struct Cue {
    int up;
    int down;

    Cue() : up(0), down(0) {}

    int total() const
    {
        return up + down;
    }

    double z() const
    {
        return double(up - down) / std::sqrt(double(up + down) + 1.0);
    }
};

struct FrameStats {
    int pairs;
    Cue ascender;
    Cue stacked;
    Cue punctuation;

    FrameStats() : pairs(0) {}

    Cue sum() const
    {
        Cue c;
        c.up = ascender.up + stacked.up + punctuation.up;
        c.down = ascender.down + stacked.down + punctuation.down;
        return c;
    }
};

// Buckets of boxes in a coarse grid, so that a neighbour search looks only at near boxes.
class BoxGrid
{
public:
    BoxGrid(std::vector<Box> const& boxes, int width, int height, int cell)
        : m_boxes(boxes), m_cell(std::max(1, cell))
    {
        m_cols = width / m_cell + 1;
        m_rows = height / m_cell + 1;
        m_cells.resize(size_t(m_cols) * size_t(m_rows));
        for (size_t i = 0; i < boxes.size(); ++i) {
            Box const& b = boxes[i];
            int const cx = std::min(m_cols - 1, std::max(0, b.x0 / m_cell));
            int const cy = std::min(m_rows - 1, std::max(0, b.y0 / m_cell));
            m_cells[size_t(cy) * size_t(m_cols) + size_t(cx)].push_back(int(i));
        }
    }

    // The grid keeps a box in the cell of its top left corner, thus the search area grows by one box size.
    template<typename F>
    void forEachNear(int x0, int y0, int x1, int y1, int grow, F f) const
    {
        int const cx0 = std::max(0, (x0 - grow) / m_cell);
        int const cy0 = std::max(0, (y0 - grow) / m_cell);
        int const cx1 = std::min(m_cols - 1, std::max(0, x1 / m_cell));
        int const cy1 = std::min(m_rows - 1, std::max(0, y1 / m_cell));
        for (int cy = cy0; cy <= cy1; ++cy) {
            for (int cx = cx0; cx <= cx1; ++cx) {
                std::vector<int> const& cell = m_cells[size_t(cy) * size_t(m_cols) + size_t(cx)];
                for (size_t k = 0; k < cell.size(); ++k) {
                    f(m_boxes[size_t(cell[k])]);
                }
            }
        }
    }
private:
    std::vector<Box> const& m_boxes;
    int m_cell;
    int m_cols;
    int m_rows;
    std::vector<std::vector<int> > m_cells;
};

// Turns a box by 90 degrees clockwise inside an image of the given height.
Box rotateBoxCw(Box const& b, int height)
{
    Box r;
    r.x0 = height - b.y1;
    r.y0 = b.x0;
    r.x1 = height - b.y0;
    r.y1 = b.x1;
    return r;
}

// Collects the evidence on the assumption that the text lines in this frame are horizontal.
FrameStats analyzeFrame(
    std::vector<Box> const& large, std::vector<Box> const& marks,
    int width, int height, double size)
{
    // Heavy print joins letters into wide boxes, and these boxes still carry the dots and accents.
    std::vector<Box> chars;
    std::vector<Box> bodies;
    for (size_t i = 0; i < large.size(); ++i) {
        Box const& b = large[i];
        if (b.height() < 0.5 * size && b.width() < 0.5 * size) {
            continue;
        }
        if (b.height() <= 2.5 * size && b.width() <= 2.5 * size) {
            chars.push_back(b);
        }
        if (b.height() <= 2.5 * size) {
            bodies.push_back(b);
        }
    }

    FrameStats stats;
    int const cell = std::max(4, int(size * 2.0));
    BoxGrid const grid(chars, width, height, cell);
    BoxGrid const body_grid(bodies, width, height, cell);
    int const grow = int(size * 3.0);
    double const tol = std::max(1.0, 0.1 * size);
    double const big = std::max(2.0, 0.22 * size);
    double const max_gap = 0.5 * size;
    double const min_gap = -0.15 * size;

    for (size_t i = 0; i < chars.size(); ++i) {
        Box const& a = chars[i];
        Box const* best = 0;
        int best_gap = 0;
        grid.forEachNear(
            a.x1 + int(min_gap) - 1, a.y0, a.x1 + int(max_gap) + 1, a.y1, grow,
        [&](Box const & b) {
            if (b.x0 <= a.x0 || b.x0 + b.x1 <= a.x0 + a.x1) {
                return;
            }
            int const gap = b.x0 - a.x1;
            if (gap < min_gap || gap > max_gap) {
                return;
            }
            int const overlap = std::min(a.y1, b.y1) - std::max(a.y0, b.y0);
            if (overlap * 2 < std::min(a.height(), b.height())) {
                return;
            }
            if (!best || gap < best_gap) {
                best = &b;
                best_gap = gap;
            }
        }
        );
        if (!best) {
            continue;
        }
        ++stats.pairs;
        int const d_bottom = std::abs(a.y1 - best->y1);
        int const d_top = std::abs(a.y0 - best->y0);
        // Latin text has more ascenders than descenders, so the bottoms of neighbour letters line up more often.
        if (d_bottom <= tol && d_top >= big) {
            ++stats.ascender.up;
        } else if (d_top <= tol && d_bottom >= big) {
            ++stats.ascender.down;
        }
    }

    int const body_grow = int(size * 15.0);
    double const stack_gap = 0.5 * size;
    double const side_gap = 0.4 * size;
    for (size_t i = 0; i < marks.size(); ++i) {
        Box const& m = marks[i];
        int const mcx2 = m.x0 + m.x1;
        int const mcy2 = m.y0 + m.y1;
        double best_above = stack_gap + 1.0;
        double best_below = stack_gap + 1.0;
        Box const* side = 0;
        bool inside = false;
        double best_side = side_gap + 1.0;
        // The dot of an i can sit a little off the narrow stem, thus the stem gets a small margin.
        int const stem_margin = int(0.15 * size);
        body_grid.forEachNear(
            m.x0 - int(side_gap) - 1, m.y0 - int(stack_gap) - 1,
            m.x1 + int(side_gap) + 1, m.y1 + int(stack_gap) + 1, body_grow,
        [&](Box const & c) {
            if (mcx2 >= 2 * (c.x0 - stem_margin) && mcx2 <= 2 * (c.x1 + stem_margin)) {
                if (m.y1 <= c.y0 + 1) {
                    best_above = std::min(best_above, double(c.y0 - m.y1));
                } else if (m.y0 >= c.y1 - 1) {
                    best_below = std::min(best_below, double(m.y0 - c.y1));
                } else {
                    inside = true;
                }
                return;
            }
            if (mcy2 < 2 * c.y0 - c.height() || mcy2 > 2 * c.y1 + c.height()) {
                return;
            }
            double const gap = m.x0 >= c.x1 ? m.x0 - c.x1 : c.x0 - m.x1;
            if (gap >= -0.1 * size && gap < best_side) {
                best_side = gap;
                side = &c;
            }
        }
        );
        // Dots and accents sit above the letters, and only ogoneks sit below them.
        if (best_above <= stack_gap || best_below <= stack_gap) {
            if (best_above < best_below) {
                ++stats.stacked.up;
            } else if (best_below < best_above) {
                ++stats.stacked.down;
            }
            continue;
        }
        // A mark inside the box of joined letters is a dot or an accent, not punctuation.
        if (!side || inside) {
            continue;
        }
        // Full stops and commas sit at the baseline of the letter next to them.
        double const rel = (0.5 * mcy2 - side->y0) / std::max(1, side->height());
        if (rel > 0.75) {
            ++stats.punctuation.up;
        } else if (rel < 0.25) {
            ++stats.punctuation.down;
        }
    }
    return stats;
}

QString cueText(Cue const& c)
{
    return QString::number(c.up) + "/" + QString::number(c.down);
}

int toDpi(int dpm)
{
    return int(std::lround(dpm * 0.0254));
}

} // namespace

double
OrientationDetector::confidenceThreshold()
{
    return 0.5;
}

OrientationResult
OrientationDetector::detect(QImage const& image)
{
    if (image.isNull()) {
        return OrientationResult(0, 0.0, false, QStringLiteral("no image"));
    }

    // A fixed scale near 300 DPI keeps the letters large enough to see ascenders and descenders.
    int const dpi = std::max(toDpi(image.dotsPerMeterX()), toDpi(image.dotsPerMeterY()));
    int const max_side = std::max(image.width(), image.height());
    int factor = 1;
    if (dpi >= 150 && dpi <= 2400) {
        factor = std::max(1, (dpi + 150) / 300);
    } else {
        factor = std::max(1, (max_side + 2999) / 3000);
    }
    while (max_side / factor > 4000) {
        ++factor;
    }

    GrayImage gray(image);
    if (factor > 1) {
        gray = scaleToGray(gray, QSize(std::max(1, image.width() / factor), std::max(1, image.height() / factor)));
    }
    int const width = gray.width();
    int const height = gray.height();

    BinaryImage bin(gray, BinaryThreshold::otsuThreshold(gray));
    gray = GrayImage();

    std::vector<Box> all;
    {
        ConnCompEraser eraser(bin.release(), CONN8);
        int const max_box = std::max(8, max_side / factor / 12);
        for (;;) {
            ConnComp const cc(eraser.nextConnComp());
            if (cc.isNull()) {
                break;
            }
            QRect const r(cc.rect());
            if (r.width() > max_box || r.height() > max_box || cc.pixCount() < 2) {
                continue;
            }
            Box b;
            b.x0 = r.left();
            b.y0 = r.top();
            b.x1 = r.right() + 1;
            b.y1 = r.bottom() + 1;
            all.push_back(b);
        }
    }

    // The median size of the letter boxes sets every distance threshold below.
    std::vector<int> sizes;
    sizes.reserve(all.size());
    for (size_t i = 0; i < all.size(); ++i) {
        if (all[i].maxSide() >= 6) {
            sizes.push_back(all[i].maxSide());
        }
    }
    if (sizes.size() < 50) {
        return OrientationResult(0, 0.0, false, QStringLiteral("no text"));
    }
    std::nth_element(sizes.begin(), sizes.begin() + sizes.size() / 2, sizes.end());
    double const size = sizes[sizes.size() / 2];

    std::vector<Box> large;
    std::vector<Box> marks;
    for (size_t i = 0; i < all.size(); ++i) {
        int const s = all[i].maxSide();
        if (s >= 0.5 * size && s <= 15.0 * size) {
            large.push_back(all[i]);
        } else if (s >= 0.12 * size && s <= 0.45 * size && s >= 2) {
            marks.push_back(all[i]);
        }
    }

    std::vector<Box> large_cw;
    std::vector<Box> marks_cw;
    large_cw.reserve(large.size());
    marks_cw.reserve(marks.size());
    for (size_t i = 0; i < large.size(); ++i) {
        large_cw.push_back(rotateBoxCw(large[i], height));
    }
    for (size_t i = 0; i < marks.size(); ++i) {
        marks_cw.push_back(rotateBoxCw(marks[i], height));
    }

    FrameStats const horizontal(analyzeFrame(large, marks, width, height, size));
    FrameStats const vertical(analyzeFrame(large_cw, marks_cw, height, width, size));

    bool const is_horizontal = horizontal.pairs >= vertical.pairs;
    FrameStats const& frame = is_horizontal ? horizontal : vertical;
    int const pairs_max = std::max(horizontal.pairs, vertical.pairs);
    int const pairs_min = std::min(horizontal.pairs, vertical.pairs);
    Cue const all_cues(frame.sum());
    double const z = all_cues.z();

    QString details = QStringLiteral("size=%1 large=%2 marks=%3 pairs=%4/%5 asc=%6 stack=%7 punct=%8 z=%9")
                      .arg(size).arg(large.size()).arg(marks.size())
                      .arg(horizontal.pairs).arg(vertical.pairs)
                      .arg(cueText(frame.ascender), cueText(frame.stacked), cueText(frame.punctuation))
                      .arg(z, 0, 'f', 2);

    // Letters in a text line have close neighbours along the line, but not across it.
    double const axis_ratio = double(pairs_max) / double(std::max(1, pairs_min));
    double const axis_conf = std::max(0.0, std::min(1.0, (axis_ratio - 1.5) / 1.5));
    double const dir_conf = std::max(0.0, std::min(1.0, (std::fabs(z) - 3.0) / 6.0));
    double const confidence = std::min(axis_conf, dir_conf);

    int angle = 0;
    if (is_horizontal) {
        angle = z >= 0 ? 0 : 180;
    } else {
        angle = z >= 0 ? 90 : 270;
    }

    OrientationResult result;
    if (pairs_max < 100) {
        result = OrientationResult(0, 0.0, false, QStringLiteral("no text"));
    } else if (axis_conf <= 0.0) {
        result = OrientationResult(0, 0.0, false, QStringLiteral("unclear text direction"));
    } else {
        // A cue that votes clearly against the others means that the page is not plain Latin text.
        Cue const cues[3] = { frame.ascender, frame.stacked, frame.punctuation };
        bool conflict = false;
        for (int i = 0; i < 3; ++i) {
            if (cues[i].z() * z < 0 && std::fabs(cues[i].z()) >= 2.0) {
                conflict = true;
            }
        }
        if (conflict) {
            result = OrientationResult(0, 0.0, false, QStringLiteral("cues disagree"));
        } else if (confidence < confidenceThreshold()) {
            result = OrientationResult(0, confidence, false, QStringLiteral("low confidence"));
        } else {
            result = OrientationResult(angle, confidence, true, QStringLiteral("text"));
        }
    }
    details += QStringLiteral(" guess=%1 conf=%2").arg(angle).arg(confidence, 0, 'f', 2);
    result.setDetails(details);
    return result;
}

OrientationResult
OrientationDetector::detect(QImage const& image, int const exif_hint)
{
    return combine(detect(image), exif_hint);
}

OrientationResult
OrientationDetector::combine(OrientationResult const& content, int const exif_hint)
{
    OrientationResult result;
    if (exif_hint < 0) {
        result = content;
    } else if (content.isDecided()) {
        QString const reason = content.angle() == exif_hint
                               ? QStringLiteral("text, exif agrees")
                               : QStringLiteral("text, exif disagrees");
        result = OrientationResult(content.angle(), content.confidence(), true, reason);
    } else if (exif_hint != 0) {
        // Without text the tag is the only evidence, thus the low confidence asks for a look at the page.
        result = OrientationResult(exif_hint, 0.3, true, QStringLiteral("exif only, ") + content.reason());
    } else {
        result = content;
    }
    result.setDetails(content.details());
    return result;
}

void
OrientationDetector::applyBookRotation(std::vector<OrientationResult>& results)
{
    int votes[4] = { 0, 0, 0, 0 };
    int decisions = 0;
    for (size_t i = 0; i < results.size(); ++i) {
        OrientationResult const& r = results[i];
        if (r.isDecided() && r.confidence() >= confidenceThreshold()) {
            ++votes[r.angle() / 90];
            ++decisions;
        }
    }
    int book = 0;
    for (int i = 1; i < 4; ++i) {
        if (votes[i] > votes[book]) {
            book = i;
        }
    }
    // Only a clear majority of several scans shows the rotation of the book.
    if (votes[book] < 5 || votes[book] * 10 < decisions * 6) {
        return;
    }
    int const book_angle = book * 90;
    for (size_t i = 0; i < results.size(); ++i) {
        OrientationResult const& r = results[i];
        if (r.isDecided() && (r.angle() - book_angle + 360) % 180 != 0) {
            OrientationResult changed(
                book_angle, 0.5 * r.confidence(), true,
                QStringLiteral("text runs across the book, book rotation used")
            );
            changed.setDetails(r.details());
            results[i] = changed;
        }
    }
}

int
OrientationDetector::exifRotation(QString const& file_path)
{
    QImageReader reader(file_path);
    if (!reader.canRead()) {
        return -1;
    }
    // A mirror tag does not occur in scans, and a rotation cannot correct a mirror, so this tag gives no hint.
    switch (int(reader.transformation())) {
    case QImageIOHandler::TransformationRotate90:
        return 90;
    case QImageIOHandler::TransformationRotate180:
        return 180;
    case QImageIOHandler::TransformationRotate270:
        return 270;
    default:
        return -1;
    }
}

} // namespace imageproc
