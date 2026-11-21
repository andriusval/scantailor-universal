/*
    Scan Tailor - Interactive post-processing tool for scanned pages.
    Copyright (C) 2020  Joseph Artsimovich <joseph.artsimovich@gmail.com>, 4lex4 <4lex49@zoho.com>

    Backported from ScanTailor Advanced (src/core/ContentMask.h).

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

#ifndef CONTENTMASK_H_
#define CONTENTMASK_H_

#include "imageproc/BinaryImage.h"
#include <QRect>
#include <QRectF>
#include <QPointF>
#include <QString>
#include <QTransform>
#include <QVector>
#include <QStringList>

namespace imageproc
{
class GrayImage;
}
class ImageTransformation;
class TaskStatus;

/**
 * \brief A binarized, despeckled 150 DPI copy of the page, used by the content box
 *        double-click adjustment: the box edge snaps to the ink found near the click.
 */
class ContentMask
{
public:
    ContentMask() {}

    ContentMask(imageproc::GrayImage const& gray_image,
                ImageTransformation const& xform, TaskStatus const& status);

    /**
     * \brief Bounding box (mask pixels) of the black pixels inside \p area,
     *        grown by 1 px; a null rect if there are none.
     */
    QRect findContentInArea(QRect const& area) const;

    /** Which box edges a snap may move (GUI: no modifier / Shift / Ctrl / Ctrl+Shift). */
    enum SnapMode {
        SNAP_NEAREST = 0, SNAP_HORIZONTAL = 1, SNAP_VERTICAL = 2, SNAP_CORNER = 3,
        // explicit edge (CLI only): that edge moves to the ink, wherever the point is in the box
        SNAP_LEFT, SNAP_RIGHT, SNAP_TOP, SNAP_BOTTOM
    };

    /**
     * \brief The content box double-click adjustment (shared by the GUI and --content-snap).
     *
     * \p content_rect, \p pos and \p bounds are in virtual (page) coordinates. Ink is looked for
     * in a 30x30 px (150 DPI) square around \p pos. If \p pos is inside the box, the nearest edge
     * (limited by \p mode) moves to that ink; otherwise the ink is added to the box.
     * \return false (and \p content_rect untouched) when there is no ink near \p pos.
     */
    bool snapContentRect(QRectF& content_rect, QPointF const& pos, SnapMode mode,
                         QRectF const& bounds, double min_size = 10.0) const;

    imageproc::BinaryImage const& image() const
    {
        return m_image;
    }

    QTransform const& originalToContentXform() const
    {
        return m_originalToContentXform;
    }

    QTransform const& contentToOriginalXform() const
    {
        return m_contentToOriginalXform;
    }

    bool isNull() const
    {
        return m_image.isNull();
    }
private:
    imageproc::BinaryImage m_image;
    QTransform m_originalToContentXform;
    QTransform m_contentToOriginalXform;
    QTransform m_virtualToContentXform;
    QTransform m_contentToVirtualXform;
};

/**
 * \brief One request of the CLI option
 *        --content-snap="<file>[:L|R]@<x>,<y>[:nearest|h|v|hv|left|right|top|bottom];..."
 *        (page coordinates as in the project's content rect; L/R for split pages).
 */
struct ContentSnapRequest {
    QString fileName;
    QChar side;            // 'L', 'R' or 0 (single page / any)
    QPointF pos;
    ContentMask::SnapMode mode;
};

/** Parses the --content-snap value; malformed entries are reported in \p errors and skipped. */
QVector<ContentSnapRequest> parseContentSnap(QString const& value, QStringList* errors = 0);

#endif
