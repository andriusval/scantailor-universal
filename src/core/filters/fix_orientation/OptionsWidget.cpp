/*
    Scan Tailor - Interactive post-processing tool for scanned pages.
    Copyright (C)  Joseph Artsimovich <joseph.artsimovich@gmail.com>

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

#include "OptionsWidget.h"

#include "Filter.h"
#include "ApplyToDialog.h"
#include "Settings.h"
#include "ProjectPages.h"
#include "ImageId.h"
#include "ImageLoader.h"
#include "PageId.h"
#include "imageproc/OrientationDetector.h"
#include <QApplication>
#include <QChar>
#include <QImage>
#include <QProgressDialog>
#include <vector>
#include <assert.h>

namespace fix_orientation
{

OptionsWidget::OptionsWidget(
    IntrusivePtr<Settings> const& settings,
    PageSelectionAccessor const& page_selection_accessor)
    :   m_ptrSettings(settings),
        m_pageSelectionAccessor(page_selection_accessor)
{
    setupUi(this);

    connect(rotateLeftBtn, SIGNAL(clicked()), this, SLOT(rotateLeft()));
    connect(rotateRightBtn, SIGNAL(clicked()), this, SLOT(rotateRight()));
    connect(resetBtn, SIGNAL(clicked()), this, SLOT(resetRotation()));
    connect(applyToBtn, SIGNAL(clicked()), this, SLOT(showApplyToDialog()));
    connect(autoBtn, SIGNAL(clicked()), this, SLOT(autoRotation()));
    connect(autoApplyBtn, SIGNAL(clicked()), this, SLOT(showAutoApplyDialog()));
    showAutoResult(QString());
}

OptionsWidget::~OptionsWidget()
{
}

void
OptionsWidget::preUpdateUI(
    PageId const& page_id, OrthogonalRotation const rotation)
{
    m_pageId = page_id;
    m_rotation = rotation;
    setRotationPixmap();
    // The result of an automatic rotation belongs to one page, so another page must not show it.
    if (m_autoResultPage != page_id) {
        showAutoResult(QString());
    }
}

void
OptionsWidget::postUpdateUI()
{
    setRotation(m_rotation);
}

void
OptionsWidget::rotateLeft()
{
    OrthogonalRotation rotation(m_rotation);
    rotation.prevClockwiseDirection();
    setRotation(rotation);
}

void
OptionsWidget::rotateRight()
{
    OrthogonalRotation rotation(m_rotation);
    rotation.nextClockwiseDirection();
    setRotation(rotation);
}

void
OptionsWidget::resetRotation()
{
    setRotation(OrthogonalRotation());
}

void
OptionsWidget::showApplyToDialog()
{
    ApplyToDialog* dialog = new ApplyToDialog(
        this, m_pageId, m_pageSelectionAccessor, PageView::IMAGE_VIEW
    );

    dialog->setWindowTitle(tr("Fix Orientation"));

    connect(dialog, &ApplyToDialog::accepted, this, [ = ]() {
        std::vector<PageId> vec = dialog->getPageRangeSelectorWidget().result();
        std::set<PageId> pages(vec.begin(), vec.end());
        if (!dialog->getPageRangeSelectorWidget().allPagesSelected()) {
            appliedTo(pages);
        } else {
            appliedToAllPages(pages);
        }
    });

    dialog->show();
}

void
OptionsWidget::appliedTo(std::set<PageId> const& pages)
{
    if (pages.empty()) {
        return;
    }

    m_ptrSettings->applyRotation(pages, m_rotation);
    for (PageId const& page_id : pages) {
        emit invalidateThumbnail(page_id);
    }
}

void
OptionsWidget::appliedToAllPages(std::set<PageId> const& pages)
{
    m_ptrSettings->applyRotation(pages, m_rotation);
    emit invalidateAllThumbnails();
}

namespace
{

OrthogonalRotation rotationFromDegrees(int const degrees)
{
    OrthogonalRotation rotation;
    for (int i = 0; i < degrees / 90; ++i) {
        rotation.nextClockwiseDirection();
    }
    return rotation;
}

imageproc::OrientationResult detectRotation(ImageId const& image_id)
{
    using imageproc::OrientationDetector;
    QImage const image(ImageLoader::load(image_id));
    if (image.isNull()) {
        return imageproc::OrientationResult(0, 0.0, false, QStringLiteral("cannot load the image"));
    }
    return OrientationDetector::detect(image, OrientationDetector::exifRotation(image_id.filePath()));
}

} // namespace

void
OptionsWidget::autoRotation()
{
    QApplication::setOverrideCursor(Qt::WaitCursor);
    imageproc::OrientationResult const result(detectRotation(m_pageId.imageId()));
    QApplication::restoreOverrideCursor();

    m_autoResultPage = m_pageId;
    if (!result.isDecided()) {
        showAutoResult(tr("Auto: no decision (%1)").arg(result.reason()));
        return;
    }
    showAutoResult(
        tr("Auto: %1%2 (%3, %4)").arg(result.angle()).arg(QChar(0x00B0))
        .arg(result.reason()).arg(result.confidence(), 0, 'f', 2)
    );
    setRotation(rotationFromDegrees(result.angle()));
}

void
OptionsWidget::showAutoApplyDialog()
{
    ApplyToDialog* dialog = new ApplyToDialog(
        this, m_pageId, m_pageSelectionAccessor, PageView::IMAGE_VIEW
    );

    dialog->setWindowTitle(tr("Automatic Orientation"));

    connect(dialog, &ApplyToDialog::accepted, this, [ = ]() {
        std::vector<PageId> vec = dialog->getPageRangeSelectorWidget().result();
        std::set<PageId> pages(vec.begin(), vec.end());
        autoAppliedTo(pages);
    });

    dialog->show();
}

void
OptionsWidget::autoAppliedTo(std::set<PageId> const& pages)
{
    std::set<ImageId> image_set;
    for (PageId const& page_id : pages) {
        image_set.insert(page_id.imageId());
    }
    std::vector<ImageId> const images(image_set.begin(), image_set.end());
    if (images.empty()) {
        return;
    }

    QProgressDialog progress(tr("Finding the rotation of the scans..."), tr("Cancel"), 0, int(images.size()), this);
    progress.setWindowModality(Qt::WindowModal);
    progress.setMinimumDuration(500);

    std::vector<imageproc::OrientationResult> results;
    for (size_t i = 0; i < images.size(); ++i) {
        progress.setValue(int(i));
        if (progress.wasCanceled()) {
            return;
        }
        results.push_back(detectRotation(images[i]));
    }
    progress.setValue(int(images.size()));

    imageproc::OrientationDetector::applyBookRotation(results);

    int turned = 0;
    int undecided = 0;
    QStringList undecided_names;
    for (size_t i = 0; i < images.size(); ++i) {
        imageproc::OrientationResult const& result = results[i];
        if (!result.isDecided()) {
            ++undecided;
            undecided_names.append(images[i].filePath().section(QLatin1Char('/'), -1));
            continue;
        }
        OrthogonalRotation const rotation(rotationFromDegrees(result.angle()));
        if (rotation != m_ptrSettings->getRotationFor(images[i])) {
            ++turned;
        }
        m_ptrSettings->applyRotation(images[i], rotation);
        if (images[i] == m_pageId.imageId() && rotation != m_rotation) {
            m_rotation = rotation;
            setRotationPixmap();
            emit rotated(rotation);
        }
    }

    m_autoResultPage = m_pageId;
    showAutoResult(
        tr("Auto: %1 scans, %2 turned, %3 without a decision")
        .arg(images.size()).arg(turned).arg(undecided)
    );
    autoResultLabel->setToolTip(undecided_names.join(QLatin1Char('\n')));
    emit invalidateAllThumbnails();
}

void
OptionsWidget::showAutoResult(QString const& text)
{
    autoResultLabel->setText(text);
    autoResultLabel->setToolTip(QString());
    autoResultLabel->setVisible(!text.isEmpty());
}

void
OptionsWidget::setRotation(OrthogonalRotation const rotation)
{
    if (rotation == m_rotation) {
        return;
    }

    m_rotation = rotation;
    setRotationPixmap();

    m_ptrSettings->applyRotation(m_pageId.imageId(), rotation);

    emit rotated(rotation);
    emit invalidateThumbnail(m_pageId);
}

void
OptionsWidget::setRotationPixmap()
{
    char const* path = 0;

    switch (m_rotation.toDegrees()) {
    case 0:
        path = ":/icons/big-up-arrow.png";
        break;
    case 90:
        path = ":/icons/big-right-arrow.png";
        break;
    case 180:
        path = ":/icons/big-down-arrow.png";
        break;
    case 270:
        path = ":/icons/big-left-arrow.png";
        break;
    default:
        assert(!"Unreachable");
    }

    rotationIndicator->setPixmap(QPixmap(path));
}

} // namespace fix_orientation
