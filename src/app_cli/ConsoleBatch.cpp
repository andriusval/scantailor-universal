/*
    Scan Tailor - Interactive post-processing tool for scanned pages.

    ConsoleBatch - Batch processing scanned pages from command line.
    Copyright (C) 2011 Petr Kovar <pejuko@gmail.com>

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

#include <vector>
#include <set>
#include <string>
#include <stdexcept>
#include <iostream>
#include <cmath>
#include <assert.h>

#include "Utils.h"
#include "ProjectPages.h"
#include "PageSelectionAccessor.h"
#include "StageSequence.h"
#include "ProcessingTaskQueue.h"
#include "FileNameDisambiguator.h"
#include "OutputFileNameGenerator.h"
#include "ImageInfo.h"
#include "ImageFileInfo.h"
#include "PageInfo.h"
#include "PageSequence.h"
#include "ImageId.h"
#include "ThumbnailPixmapCache.h"
#include "LoadFileTask.h"
#include "ProjectWriter.h"
#include "ProjectReader.h"
#include "OrthogonalRotation.h"
#include "SelectedPage.h"
#include "ImageLoader.h"
#include "ImageTransformation.h"
#include "ContentBoxCollector.h"
#include "settings/ini_keys.h"

#include "filters/fix_orientation/Settings.h"
#include "filters/fix_orientation/Filter.h"
#include "filters/fix_orientation/Task.h"
#include "filters/fix_orientation/CacheDrivenTask.h"
#include "filters/page_split/Settings.h"
#include "filters/page_split/Filter.h"
#include "filters/page_split/Task.h"
#include "filters/page_split/CacheDrivenTask.h"
#include "filters/deskew/Settings.h"
#include "filters/deskew/Filter.h"
#include "filters/deskew/Task.h"
#include "filters/deskew/CacheDrivenTask.h"
#include "filters/select_content/Settings.h"
#include "filters/select_content/Filter.h"
#include "filters/select_content/Task.h"
#include "filters/select_content/CacheDrivenTask.h"
#include "filters/page_layout/Settings.h"
#include "filters/page_layout/Filter.h"
#include "filters/page_layout/Task.h"
#include "filters/page_layout/CacheDrivenTask.h"
#include "filters/output/Settings.h"
#include "filters/output/Params.h"
#include "filters/output/OutputParams.h"
#include "filters/output/Filter.h"
#include "filters/output/Task.h"
#include "filters/output/CacheDrivenTask.h"

#include <QDir>
#include <QMap>
#include <QDir>
#include <QFileInfo>
#include <QImage>
#include <QPainter>
#include <QPainterPath>
#include <QDir>
#include <QFileInfo>
#include <QDomDocument>
#include <QRegularExpression>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <cstdio>
#include <stdexcept>

#include "MultipleTargetsSupport.h"

#include "ConsoleBatch.h"
#include "CommandLine.h"

namespace
{

class PreviewCollector : public ContentBoxCollector
{
public:
    PreviewCollector()
        :   m_xform(QRectF(0, 0, 1, 1), Dpi(300, 300)),
            m_collected(false)
    {
    }

    virtual void process(ImageTransformation const& xform, QRectF const& content_rect)
    {
        m_xform = xform;
        m_contentRect = content_rect;
        m_collected = true;
    }

    bool collected() const
    {
        return m_collected;
    }

    ImageTransformation const& xform() const
    {
        return m_xform;
    }

    QRectF const& contentRect() const
    {
        return m_contentRect;
    }
private:
    ImageTransformation m_xform;
    QRectF m_contentRect;
    bool m_collected;
};

// Draws the boxes as select_content::ImageView::onPaint() does, to show the page as the GUI shows it.
void drawPreviewBoxes(
    QPainter& painter, QRectF const& display_rect,
    QRectF const& page_rect, QRectF const& content_rect, QColor const& content_color)
{
    if (content_rect.isNull()) {
        return;
    }

    if (!page_rect.isNull() && page_rect != display_rect) {
        QPen pen(QColor(0xee, 0xee, 0x00, 0xcc));
        pen.setWidth(1);
        pen.setCosmetic(true);
        painter.setPen(pen);
        painter.setBrush(QColor(0xee, 0xee, 0x00, 0xcc));

        QRectF const& r = display_rect;
        painter.drawRect(QRectF(QPointF(page_rect.left(), r.top()), QPointF(page_rect.right(), page_rect.top())));
        painter.drawRect(QRectF(QPointF(r.left(), r.top()), QPointF(page_rect.left(), r.bottom())));
        painter.drawRect(QRectF(QPointF(page_rect.left(), page_rect.bottom()), QPointF(page_rect.right(), r.bottom())));
        painter.drawRect(QRectF(QPointF(page_rect.right(), r.top()), QPointF(r.right(), r.bottom())));
    }

    painter.setRenderHints(QPainter::Antialiasing, true);

    QColor pen_color(content_color);
    pen_color.setAlpha(255);
    QPen pen(pen_color);
    pen.setWidth(1);
    pen.setCosmetic(true);
    painter.setPen(pen);
    painter.setBrush(content_color);
    painter.drawRect(content_rect);
}

QImage renderPreview(
    QImage const& image, ImageTransformation const& xform,
    QRectF const& page_rect, QRectF const& content_rect,
    QColor const& content_color, int const max_size)
{
    QPolygonF const crop_area(xform.resultingPreCropArea());
    QRectF const display_rect(crop_area.boundingRect());
    double const long_side = std::max(display_rect.width(), display_rect.height());
    double const scale = long_side > max_size ? max_size / long_side : 1.0;

    QImage preview(
        std::max(1, qRound(display_rect.width() * scale)),
        std::max(1, qRound(display_rect.height() * scale)),
        QImage::Format_RGB32
    );
    preview.fill(QColor(0xef, 0xef, 0xef));

    QTransform virtual_to_preview;
    virtual_to_preview.scale(scale, scale);
    virtual_to_preview.translate(-display_rect.left(), -display_rect.top());

    // Scale the image smoothly first, because a direct transform of a large scan gives jagged lines.
    QTransform const& t = xform.transform();
    double const scale_x = std::min(1.0, scale * std::hypot(t.m11(), t.m12()));
    double const scale_y = std::min(1.0, scale * std::hypot(t.m21(), t.m22()));
    QImage const small_image(
        (scale_x < 1.0 || scale_y < 1.0) ? image.scaled(
            std::max(1, qRound(image.width() * scale_x)),
            std::max(1, qRound(image.height() * scale_y)),
            Qt::IgnoreAspectRatio, Qt::SmoothTransformation
        ) : image
    );
    QTransform const pixmap_to_image(QTransform::fromScale(
        double(image.width()) / small_image.width(),
        double(image.height()) / small_image.height()
    ));

    QPainter painter(&preview);
    painter.setRenderHint(QPainter::SmoothPixmapTransform, true);

    // Clip to the crop area only, because QPolygonF::intersected() with the image area gives an empty polygon for some deskewed pages.
    QPainterPath clip_path;
    clip_path.addPolygon(crop_area);
    painter.setWorldTransform(virtual_to_preview);
    painter.setClipPath(clip_path);

    painter.setWorldTransform(pixmap_to_image * xform.transform() * virtual_to_preview);
    painter.drawImage(QPointF(0, 0), small_image);

    painter.setClipping(false);
    painter.setWorldTransform(virtual_to_preview);
    drawPreviewBoxes(painter, display_rect, page_rect, content_rect, content_color);
    painter.end();

    return preview;
}

} // namespace

ConsoleBatch::ConsoleBatch(std::vector<ImageFileInfo> const& images, QString const& output_directory, Qt::LayoutDirection const layout)
    :   batch(true), debug(true), m_pageErrors(0), m_warnings(0),
        m_ptrDisambiguator(new FileNameDisambiguator),
        m_ptrPages(new ProjectPages(images, ProjectPages::AUTO_PAGES, layout)),
        m_hasPageFilter(false)
{
    PageSelectionAccessor const accessor((IntrusivePtr<PageSelectionProvider>())); // Won't really be used anyway.
    m_ptrStages = IntrusivePtr<StageSequence>(new StageSequence(m_ptrPages, accessor));

    //m_ptrThumbnailCache = IntrusivePtr<ThumbnailPixmapCache>(new ThumbnailPixmapCache(output_dir+"/cache/thumbs", QSize(200,200), 40, 5));
    m_ptrThumbnailCache = Utils::createThumbnailCache(output_directory);
    m_outFileNameGen = OutputFileNameGenerator(m_ptrDisambiguator, output_directory, m_ptrPages->layoutDirection());
}

ConsoleBatch::ConsoleBatch(QString const project_file)
    :   batch(true), debug(true), m_pageErrors(0), m_warnings(0), m_hasPageFilter(false)
{
    QFile file(project_file);
    if (!file.open(QIODevice::ReadOnly)) {
        throw std::runtime_error("Unable to open the project file.");
    }

    QDomDocument doc;
    if (!doc.setContent(&file)) {
        throw std::runtime_error("The project file is broken.");
    }

    file.close();

    m_ptrReader.reset(new ProjectReader(doc, project_file));
    m_ptrPages = m_ptrReader->pages();

    PageSelectionAccessor const accessor((IntrusivePtr<PageSelectionProvider>())); // Won't be used anyway.
    m_ptrDisambiguator = m_ptrReader->namingDisambiguator();

    m_ptrStages = IntrusivePtr<StageSequence>(new StageSequence(m_ptrPages, accessor));
    m_ptrReader->readFilterSettings(m_ptrStages->filters());

    CommandLine const& cli = CommandLine::get();
    QString output_directory = m_ptrReader->outputDirectory();
    if (!cli.outputDirectory().isEmpty()) {
        output_directory = cli.outputDirectory();
    }

    //m_ptrThumbnailCache = IntrusivePtr<ThumbnailPixmapCache>(new ThumbnailPixmapCache(output_directory+"/cache/thumbs", QSize(200,200), 40, 5));
    m_ptrThumbnailCache = Utils::createThumbnailCache(output_directory);
    m_outFileNameGen = OutputFileNameGenerator(m_ptrDisambiguator, output_directory, m_ptrPages->layoutDirection());
}

IntrusivePtr<LoadFileTask>
ConsoleBatch::createCompositeTask(
    PageInfo const& page,
    int const last_filter_idx)
{
    IntrusivePtr<fix_orientation::Task> fix_orientation_task;
    IntrusivePtr<page_split::Task> page_split_task;
    IntrusivePtr<deskew::Task> deskew_task;
    IntrusivePtr<select_content::Task> select_content_task;
    IntrusivePtr<page_layout::Task> page_layout_task;
    IntrusivePtr<output::Task> output_task;

    if (batch) {
        debug = false;
    }

    if (last_filter_idx >= m_ptrStages->outputFilterIdx()) {
        output_task = m_ptrStages->outputFilter()->createTask(
                          page.id(), m_ptrThumbnailCache, m_outFileNameGen, batch, debug
                      );
        debug = false;
    }
    if (last_filter_idx >= m_ptrStages->pageLayoutFilterIdx()) {
        page_layout_task = m_ptrStages->pageLayoutFilter()->createTask(
                               page.id(), output_task, batch, debug
                           );
        debug = false;
    }
    if (last_filter_idx >= m_ptrStages->selectContentFilterIdx()) {
        select_content_task = m_ptrStages->selectContentFilter()->createTask(
                                  page.id(), page_layout_task, batch, debug
                              );
        debug = false;
    }
    if (last_filter_idx >= m_ptrStages->deskewFilterIdx()) {
        deskew_task = m_ptrStages->deskewFilter()->createTask(
                          page.id(), select_content_task, batch, debug
                      );
        debug = false;
    }
    if (last_filter_idx >= m_ptrStages->pageSplitFilterIdx()) {
        page_split_task = m_ptrStages->pageSplitFilter()->createTask(
                              page, deskew_task, batch, debug
                          );
        debug = false;
    }
    if (last_filter_idx >= m_ptrStages->fixOrientationFilterIdx()) {
        fix_orientation_task = m_ptrStages->fixOrientationFilter()->createTask(
                                   page.id(), page_split_task, batch
                               );
        debug = false;
    }
    assert(fix_orientation_task);

    return IntrusivePtr<LoadFileTask>(
               new LoadFileTask(
                   BackgroundTask::BATCH,
                   page, m_ptrThumbnailCache, m_ptrPages, fix_orientation_task
               )
           );
}

// The GUI uses a Qt resource image for empty pages, and the CLI cannot load it, so the CLI skips these pages as before.
static bool
isResourcePath(QString const& path)
{
    return path.startsWith(QLatin1Char(':'));
}

void
ConsoleBatch::checkImageFiles()
{
    bool const json = CommandLine::get().isJsonProgress();
    std::set<QString> checked;
    int bad_files = 0;
    PageSequence const images = m_ptrPages->toPageSequence(IMAGE_VIEW);
    for (PageInfo const& page : images) {
        QString const path = page.imageId().filePath();
        if (isResourcePath(path) || !checked.insert(path).second) {
            continue;
        }
        QFileInfo const file(path);
        QString const native_path = QDir::toNativeSeparators(file.absoluteFilePath());
        QString error;
        if (!file.exists()) {
            std::cerr << "Error: The image file does not exist: "
                      << native_path.toLocal8Bit().constData() << std::endl;
            error = QStringLiteral("the image file does not exist");
        } else if (!file.isFile() || !file.isReadable()) {
            std::cerr << "Error: The image file cannot be read: "
                      << native_path.toLocal8Bit().constData() << std::endl;
            error = QStringLiteral("the image file cannot be read");
        }
        if (error.isEmpty()) {
            continue;
        }
        ++bad_files;
        ++m_pageErrors;
        if (json) {
            // The CLI does this test before the first stage, thus the line has no stage and no page side.
            QJsonObject ev;
            ev["event"] = QStringLiteral("page_error");
            ev["image"] = file.fileName();
            ev["image_path"] = native_path;
            ev["error"] = error;
            printJsonLine(ev);
        }
    }

    if (bad_files > 0) {
        throw std::runtime_error(
            "Error: " + std::to_string(bad_files) + " image file(s) are missing or cannot be read. No page was processed."
        );
    }
}

// process the image vector **images** and save output to **output_dir**
void
ConsoleBatch::process()
{
    CommandLine const& cli = CommandLine::get();

    // Stop before the filters write output or change settings, because these images will fail later.
    checkImageFiles();

    // get first filter id
    int startFilterIdx = m_ptrStages->fixOrientationFilterIdx();
    if (cli.hasStartFilterIdx()) {
        unsigned int sf = cli.getStartFilterIdx();
        if (sf >= m_ptrStages->filters().size()) {
            throw std::runtime_error("Start filter out of range");
        }
        startFilterIdx = sf;
    }

    // get last filter id
    int endFilterIdx = m_ptrStages->outputFilterIdx();
    if (cli.hasEndFilterIdx()) {
        unsigned int ef = cli.getEndFilterIdx();
        if (ef >= m_ptrStages->filters().size()) {
            throw std::runtime_error("End filter out of range");
        }
        endFilterIdx = ef;
    }

    if (cli.hasPages()) {
        parsePageFilter(cli.pages());
    }

    bool const json = cli.isJsonProgress();

    // run filters
    for (int j = startFilterIdx; j <= endFilterIdx; j++) {
        if (cli.isVerbose()) {
            std::cout << "Filter: " << (j + 1) << "\n";
        }

        // Pages outside --pages still count in the aggregate size, as in a GUI batch from the current page.
        bool const check_agg_size = m_hasPageFilter && j == m_ptrStages->pageLayoutFilterIdx();
        QSizeF const agg_size_before = check_agg_size
                                       ? m_ptrStages->pageLayoutFilter()->getSettings()->getAggregateHardSizeMM()
                                       : QSizeF();

        // process pages
        PageSequence page_sequence = m_ptrPages->toPageSequence(PAGE_VIEW);
        std::set<PageId> const selection = selectedPages(page_sequence);
        setupFilter(j, selection);
        int failed_pages = 0;

        int const num_pages = int(selection.size());
        if (json) {
            QJsonObject ev;
            ev["event"] = QStringLiteral("stage_start");
            ev["stage"] = j + 1;
            ev["name"] = stageName(j);
            ev["pages"] = num_pages;
            printJsonLine(ev);
        }

        int page_index = 0;
        for (const PageInfo& page : page_sequence) {
            if (selection.count(page.id()) == 0) {
                continue;
            }
            if (cli.isVerbose()) {
                std::cout << "\tProcessing: " << page.imageId().filePath().toLocal8Bit().constData() << "\n";
            }

            QJsonObject ev;
            if (json) {
                ev["stage"] = j + 1;
                ev["name"] = stageName(j);
                ev["index"] = page_index;
                ev["pages"] = num_pages;
                ev["image"] = QFileInfo(page.imageId().filePath()).fileName();
                if (page.imageId().isMultiPageFile()) {
                    ev["image_page"] = page.imageId().page();
                }
                ev["side"] = page.id().subPageAsString();
            }
            ++page_index;

            IntrusivePtr<LoadFileTask> bgTask = createCompositeTask(page, j);
            try {
                (*bgTask)();
            } catch (std::exception const& e) {
                ++m_pageErrors;
                if (json) {
                    ev["event"] = QStringLiteral("page_error");
                    ev["error"] = QString::fromLocal8Bit(e.what());
                    printJsonLine(ev);
                }
                throw;
            }

            if (bgTask->loadFailed() && !isResourcePath(page.imageId().filePath())) {
                std::cerr << "Error: The image cannot be loaded: "
                          << QDir::toNativeSeparators(QFileInfo(page.imageId().filePath()).absoluteFilePath()).toLocal8Bit().constData();
                if (page.imageId().isMultiPageFile()) {
                    std::cerr << " (page " << page.imageId().page() << ")";
                }
                std::cerr << std::endl;
                ++failed_pages;
                ++m_pageErrors;
                if (json) {
                    ev["event"] = QStringLiteral("page_error");
                    ev["error"] = QStringLiteral("cannot load the image");
                    printJsonLine(ev);
                }
                continue;
            }

            if (json) {
                ev["event"] = QStringLiteral("page_done");
                QJsonObject const res(pageResult(j, page.id()));
                for (QJsonObject::const_iterator it = res.constBegin(); it != res.constEnd(); ++it) {
                    ev.insert(it.key(), it.value());
                }
                printJsonLine(ev);
            }
        }
        if (failed_pages > 0) {
            throw std::runtime_error(
                "Error: " + std::to_string(failed_pages) + " page(s) failed because their images cannot be loaded."
            );
        }

        if (check_agg_size &&
                agg_size_before != m_ptrStages->pageLayoutFilter()->getSettings()->getAggregateHardSizeMM()) {
            ++m_warnings;
            std::cerr << "Warning: the aggregate page size changed. The pages that are not in --pages"
                      << " keep their old output size. Run stages 5-6 on all pages to make the sizes equal again."
                      << std::endl;
            if (json) {
                QJsonObject ev;
                ev["event"] = QStringLiteral("warning");
                ev["stage"] = j + 1;
                ev["name"] = stageName(j);
                ev["warning"] = QStringLiteral("aggregate_size_changed");
                ev["message"] = QStringLiteral(
                    "The aggregate page size changed. Run stages 5-6 on all pages to make the sizes equal again.");
                printJsonLine(ev);
            }
        }
    }

    // setup rest filters with params from cli
    const std::set<PageId> select_all = selectedPages(m_ptrPages->toPageSequence(PAGE_VIEW));
    for (int j = endFilterIdx + 1; j <= m_ptrStages->count(); j++) {
        setupFilter(j, select_all);
    }

    // update statistics for executed filters
    for (int j = 0; j <= endFilterIdx; j++) {
        m_ptrStages->filterAt(j)->updateStatistics();
    }
}

int
ConsoleBatch::renderPreviews(QString const& directory, int const max_size)
{
    CommandLine const& cli = CommandLine::get();
    QDir const dir(directory);
    if (!dir.mkpath(".")) {
        throw std::runtime_error("Unable to create the preview directory.");
    }

    // Use the GUI cache tasks, and give select_content a next task, because without it the task changes the thumbnail flags.
    IntrusivePtr<select_content::CacheDrivenTask> const select_content_task(
        m_ptrStages->selectContentFilter()->createCacheDrivenTask(
            m_ptrStages->pageLayoutFilter()->createCacheDrivenTask(
                IntrusivePtr<output::CacheDrivenTask>()
            )
        )
    );
    IntrusivePtr<fix_orientation::CacheDrivenTask> const task(
        m_ptrStages->fixOrientationFilter()->createCacheDrivenTask(
            m_ptrStages->pageSplitFilter()->createCacheDrivenTask(
                m_ptrStages->deskewFilter()->createCacheDrivenTask(select_content_task)
            )
        )
    );

    QSettings const settings;
    QColor const content_color(
        settings.value(_key_content_sel_content_color, _key_content_sel_content_color_def).toString()
    );

    int failures = 0;
    ImageId loaded_image_id;
    QImage image;
    // With --pages, only the selected pages get a preview, because the other pages did not change.
    PageSequence const preview_pages(m_ptrPages->toPageSequence(PAGE_VIEW));
    std::set<PageId> const preview_selection = selectedPages(preview_pages);
    for (PageInfo const& page : preview_pages) {
        if (preview_selection.count(page.id()) == 0) {
            continue;
        }
        QString const file_name(
            QFileInfo(m_outFileNameGen.fileNameFor(page.id())).completeBaseName() + ".png"
        );

        // A page marked for a new thumbnail still has a valid content box, so clear the mark for this pass and restore it.
        select_content::Settings* const sc_settings = m_ptrStages->selectContentFilter()->getSettings();
        std::unique_ptr<select_content::Params> const sc_params(sc_settings->getPageParams(page.id()));
        bool const thumbnail_mark = sc_params.get()
                                    && (sc_params->getForceReprocess() & select_content::Params::RegenerateThumbnail);
        if (thumbnail_mark) {
            select_content::Params cleared(*sc_params);
            cleared.setForceReprocess(select_content::Params::Regenerate(
                cleared.getForceReprocess() & ~select_content::Params::RegenerateThumbnail));
            sc_settings->setPageParams(page.id(), cleared);
        }

        PreviewCollector collector;
        task->process(page, &collector);
        if (thumbnail_mark) {
            sc_settings->setPageParams(page.id(), *sc_params);
        }
        if (!collector.collected()) {
            std::cerr << "No preview for " << file_name.toLocal8Bit().constData()
                      << ": filters 1-4 are not complete for this page." << std::endl;
            ++failures;
            continue;
        }

        QRectF page_rect;
        std::unique_ptr<select_content::Params> const params(
            m_ptrStages->selectContentFilter()->getSettings()->getPageParams(page.id())
        );
        if (params.get()) {
            page_rect = params->pageRect();
        }

        // The two pages of a split image use the same image, thus load it one time only.
        if (!(loaded_image_id == page.imageId())) {
            image = ImageLoader::load(page.imageId());
            loaded_image_id = page.imageId();
        }
        if (image.isNull()) {
            std::cerr << "Unable to load " << page.imageId().filePath().toLocal8Bit().constData() << std::endl;
            ++failures;
            continue;
        }

        QImage const preview(
            renderPreview(image, collector.xform(), page_rect, collector.contentRect(), content_color, max_size)
        );
        QString const path(dir.filePath(file_name));
        if (!preview.save(path, "PNG")) {
            std::cerr << "Unable to write " << path.toLocal8Bit().constData() << std::endl;
            ++failures;
        } else if (cli.isVerbose()) {
            std::cout << "Preview: " << path.toLocal8Bit().constData() << "\n";
        }
    }

    return failures;
}

void
ConsoleBatch::parsePageFilter(QString const& value)
{
    static QRegularExpression const index_rx("^#?(\\d+)(?:-(\\d+))?$");
    static QRegularExpression const file_rx("^(.+?)(?::([LRlr]))?$");
#ifdef _WIN32
    Qt::CaseSensitivity const cs = Qt::CaseInsensitive;
#else
    Qt::CaseSensitivity const cs = Qt::CaseSensitive;
#endif

    PageSequence const pages = m_ptrPages->toPageSequence(PAGE_VIEW);
    QStringList unknown;
    QStringList ambiguous;
    for (QString const& raw : value.split(';', QStringSkipEmptyParts)) {
        QString const item(raw.trimmed());
        if (item.isEmpty()) {
            continue;
        }
        bool found = false;
        QRegularExpressionMatch const index_m(index_rx.match(item));
        if (index_m.hasMatch()) {
            if (!item.startsWith('#')) {
                // A bare number that is also an image name without extension is probably a file, not an index.
                for (PageInfo const& page : pages) {
                    if (QFileInfo(page.imageId().filePath()).completeBaseName().compare(item, cs) == 0) {
                        ambiguous.append(item);
                        break;
                    }
                }
            }
            bool first_ok = false;
            bool last_ok = true;
            qulonglong const first = index_m.captured(1).toULongLong(&first_ok);
            qulonglong const last = index_m.captured(2).isEmpty()
                                    ? first : index_m.captured(2).toULongLong(&last_ok);
            if (first_ok && last_ok && first <= last && last < pages.numPages()) {
                for (qulonglong i = first; i <= last; ++i) {
                    PageId const& id = pages.pageAt(size_t(i)).id();
                    // Select the image, so that both halves stay selected if page_split cuts it in two later.
                    if (id.subPage() == PageId::SINGLE_PAGE) {
                        m_filterImages.insert(id.imageId());
                    } else {
                        m_filterPages.insert(id);
                    }
                }
                found = true;
            }
        } else {
            QRegularExpressionMatch const file_m(file_rx.match(item));
            QString want(QDir::cleanPath(QDir::fromNativeSeparators(file_m.captured(1))));
            while (want.startsWith("../")) {
                want.remove(0, 3);
            }
            QChar const side = file_m.captured(2).isEmpty() ? QChar() : file_m.captured(2).toUpper().at(0);
            std::set<ImageId> matched_images;
            for (PageInfo const& page : pages) {
                // Compare the path to tell apart two scans with the same name in different folders.
                QString const file_path(QDir::fromNativeSeparators(page.imageId().filePath()));
                bool const same_file = want.contains('/')
                                       ? (file_path.compare(want, cs) == 0 ||
                                          file_path.endsWith(QChar('/') + want, cs))
                                       : want.compare(QFileInfo(file_path).fileName(), cs) == 0;
                if (!same_file) {
                    continue;
                }
                matched_images.insert(page.imageId());
                PageId::SubPage const sub_page = page.id().subPage();
                if (side.isNull()) {
                    m_filterImages.insert(page.imageId());
                    found = true;
                } else if ((side == 'L' && sub_page == PageId::LEFT_PAGE) ||
                           (side == 'R' && sub_page == PageId::RIGHT_PAGE)) {
                    m_filterPages.insert(page.id());
                    found = true;
                }
            }
            if (matched_images.size() > 1) {
                ambiguous.append(item);
            }
        }
        if (!found) {
            unknown.append(item);
        }
    }

    if (!unknown.isEmpty()) {
        throw std::runtime_error(
            ("Error: --pages: no page matches \"" + unknown.join("\", \"") + "\"").toLocal8Bit().constData()
        );
    }
    if (!ambiguous.isEmpty()) {
        throw std::runtime_error(
            ("Error: --pages: more than one image matches \"" + ambiguous.join("\", \"")
             + "\"; give a longer path, or #<index> for a page index").toLocal8Bit().constData()
        );
    }
    if (m_filterPages.empty() && m_filterImages.empty()) {
        throw std::runtime_error("Error: --pages: the list is empty");
    }
    m_hasPageFilter = true;
}

std::set<PageId>
ConsoleBatch::selectedPages(PageSequence const& pages) const
{
    std::set<PageId> const current = pages.asPageIdSet();
    if (!m_hasPageFilter) {
        return current;
    }

    // A half that this run removed with a new split must not drop its image from the selection.
    std::set<ImageId> images(m_filterImages);
    for (PageId const& id : m_filterPages) {
        if (current.count(id) == 0) {
            images.insert(id.imageId());
        }
    }

    std::set<PageId> selection;
    for (PageId const& id : current) {
        if (m_filterPages.count(id) != 0 || images.count(id.imageId()) != 0) {
            selection.insert(id);
        }
    }
    return selection;
}

bool
ConsoleBatch::saveProject(QString const project_file)
{
    PageInfo fpage = m_ptrPages->toPageSequence(PAGE_VIEW).pageAt(0);
    SelectedPage sPage(fpage.id(), IMAGE_VIEW);
    ProjectWriter writer(m_ptrPages, sPage, m_outFileNameGen);
    return writer.write(project_file, m_ptrStages->filters());
}

int
ConsoleBatch::pageCount() const
{
    return int(m_ptrPages->toPageSequence(PAGE_VIEW).numPages());
}

void
ConsoleBatch::printJsonLine(QJsonObject const& obj)
{
    // The JSON goes to the C stdout stream because the CLI sends std::cout text to stderr in this mode.
    QByteArray const line(QJsonDocument(obj).toJson(QJsonDocument::Compact));
    std::fwrite(line.constData(), 1, size_t(line.size()), stdout);
    std::fputc('\n', stdout);
    std::fflush(stdout);
}

QString
ConsoleBatch::stageName(int idx) const
{
    if (idx == m_ptrStages->fixOrientationFilterIdx()) {
        return QStringLiteral("fix_orientation");
    } else if (idx == m_ptrStages->pageSplitFilterIdx()) {
        return QStringLiteral("page_split");
    } else if (idx == m_ptrStages->deskewFilterIdx()) {
        return QStringLiteral("deskew");
    } else if (idx == m_ptrStages->selectContentFilterIdx()) {
        return QStringLiteral("select_content");
    } else if (idx == m_ptrStages->pageLayoutFilterIdx()) {
        return QStringLiteral("page_layout");
    } else if (idx == m_ptrStages->outputFilterIdx()) {
        return QStringLiteral("output");
    }
    return QString();
}

static QJsonObject
rectToJson(QRectF const& rect)
{
    QJsonObject obj;
    obj["x"] = rect.x();
    obj["y"] = rect.y();
    obj["width"] = rect.width();
    obj["height"] = rect.height();
    return obj;
}

static QJsonObject
lineToJson(QLineF const& line)
{
    QJsonObject obj;
    obj["x1"] = line.x1();
    obj["y1"] = line.y1();
    obj["x2"] = line.x2();
    obj["y2"] = line.y2();
    return obj;
}

static QJsonObject
marginsToJson(Margins const& margins)
{
    QJsonObject obj;
    obj["left"] = margins.left();
    obj["top"] = margins.top();
    obj["right"] = margins.right();
    obj["bottom"] = margins.bottom();
    return obj;
}

static QString
modeToString(AutoManualMode mode)
{
    return mode == MODE_MANUAL ? QStringLiteral("manual") : QStringLiteral("auto");
}

QJsonObject
ConsoleBatch::pageResult(int idx, PageId const& page_id) const
{
    QJsonObject res;
    if (idx == m_ptrStages->fixOrientationFilterIdx()) {
        res["rotation"] = m_ptrStages->fixOrientationFilter()->getSettings()
                          ->getRotationFor(page_id.imageId()).toDegrees();
    } else if (idx == m_ptrStages->pageSplitFilterIdx()) {
        page_split::Settings::Record const record(
            m_ptrStages->pageSplitFilter()->getSettings()->getPageRecord(page_id.imageId()));
        page_split::Params const* params = record.params();
        if (params) {
            page_split::PageLayout const& layout = params->pageLayout();
            res["layout"] = page_split::layoutTypeToString(layout.toLayoutType());
            res["split_mode"] = modeToString(params->splitLineMode());
            res["sub_pages"] = layout.numSubPages();
            QJsonArray cutters;
            for (int i = 0; i < layout.numCutters(); ++i) {
                cutters.append(lineToJson(layout.cutterLine(i)));
            }
            res["cutters"] = cutters;
        } else {
            res["layout"] = page_split::layoutTypeToString(record.combinedLayoutType());
        }
    } else if (idx == m_ptrStages->deskewFilterIdx()) {
        std::unique_ptr<deskew::Params> const params(
            m_ptrStages->deskewFilter()->getSettings()->getPageParams(page_id));
        if (params) {
            res["angle"] = params->deskewAngle();
            res["mode"] = modeToString(params->mode());
        }
    } else if (idx == m_ptrStages->selectContentFilterIdx()) {
        std::unique_ptr<select_content::Params> const params(
            m_ptrStages->selectContentFilter()->getSettings()->getPageParams(page_id));
        if (params) {
            res["content_rect"] = rectToJson(params->contentRect());
            res["page_rect"] = rectToJson(params->pageRect());
            res["mode"] = modeToString(params->mode());
        }
    } else if (idx == m_ptrStages->pageLayoutFilterIdx()) {
        page_layout::Settings const* settings = m_ptrStages->pageLayoutFilter()->getSettings();
        page_layout::Alignment const alignment(settings->getPageAlignment(page_id));
        MarginsWithAuto const margins(settings->getHardMarginsMM(page_id));
        QJsonObject margins_json(marginsToJson(margins));
        margins_json["auto"] = margins.isAutoMarginsEnabled();
        res["margins_mm"] = margins_json;
        QJsonObject align;
        align["vert"] = page_layout::Alignment::verticalToStr(alignment.vertical());
        align["hor"] = page_layout::Alignment::horizontalToStr(alignment.horizontal());
        align["null"] = alignment.isNull();
        res["alignment"] = align;
    } else if (idx == m_ptrStages->outputFilterIdx()) {
        output::Params const params(m_ptrStages->outputFilter()->getSettings()->getParams(page_id));
        QString const out_path(m_outFileNameGen.filePathFor(page_id));
        res["output_file"] = out_path;
        res["dpi"] = params.outputDpi().horizontal();
        switch (params.colorParams().colorMode()) {
        case output::ColorParams::BLACK_AND_WHITE:
            res["color_mode"] = QStringLiteral("bw");
            break;
        case output::ColorParams::COLOR_GRAYSCALE:
            res["color_mode"] = QStringLiteral("colorOrGray");
            break;
        case output::ColorParams::MIXED:
            res["color_mode"] = QStringLiteral("mixed");
            break;
        }
        output::GrayscaleFirst const grayscale_first = params.colorParams().colorGrayscaleOptions().grayscaleFirst();
        res["grayscale_first"] = grayscale_first == output::GRAYSCALE_FIRST_ON ? QStringLiteral("on")
                                 : grayscale_first == output::GRAYSCALE_FIRST_AUTO ? QStringLiteral("auto")
                                 : QStringLiteral("off");
        std::unique_ptr<output::OutputParams> const output_params(
            m_ptrStages->outputFilter()->getSettings()->getOutputParams(page_id));
        if (output_params && output_params->outputImageParams().grayscaleChroma() >= 0.0) {
            res["grayscale_detected"] = output_params->outputImageParams().grayscaleDetected();
            res["chroma"] = qRound(output_params->outputImageParams().grayscaleChroma() * 10.0) / 10.0;
        }
    }
    return res;
}

void
ConsoleBatch::setupFilter(int idx, std::set<PageId> allPages)
{
    if (idx == m_ptrStages->fixOrientationFilterIdx()) {
        setupFixOrientation(allPages);
    } else if (idx == m_ptrStages->pageSplitFilterIdx()) {
        setupPageSplit(allPages);
    } else if (idx == m_ptrStages->deskewFilterIdx()) {
        setupDeskew(allPages);
    } else if (idx == m_ptrStages->selectContentFilterIdx()) {
        setupSelectContent(allPages);
    } else if (idx == m_ptrStages->pageLayoutFilterIdx()) {
        setupPageLayout(allPages);
    } else if (idx == m_ptrStages->outputFilterIdx()) {
        setupOutput(allPages);
    }
}

void
ConsoleBatch::setupFixOrientation(std::set<PageId> allPages)
{
    IntrusivePtr<fix_orientation::Filter> fix_orientation = m_ptrStages->fixOrientationFilter();
    CommandLine const& cli = CommandLine::get();

    for (PageId const& page : allPages) {

        OrthogonalRotation rotation;
        // FIX ORIENTATION FILTER
        if (cli.hasOrientation()) {
            switch (cli.getOrientation()) {
            case CommandLine::LEFT:
                rotation.prevClockwiseDirection();
                break;
            case CommandLine::RIGHT:
                rotation.nextClockwiseDirection();
                break;
            case CommandLine::UPSIDEDOWN:
                rotation.nextClockwiseDirection();
                rotation.nextClockwiseDirection();
                break;
            default:
                break;
            }
            fix_orientation->getSettings()->applyRotation(page.imageId(), rotation);
        }
    }
}

void
ConsoleBatch::setupPageSplit(std::set<PageId> allPages)
{
    IntrusivePtr<page_split::Filter> page_split = m_ptrStages->pageSplitFilter();
    CommandLine const& cli = CommandLine::get();

    // PAGE SPLIT
    if (cli.hasLayout()) {
        if (!m_hasPageFilter) {
            page_split->getSettings()->setLayoutTypeForAllPages(cli.getLayout());
        } else {
            // Same per-image update as the GUI "apply to" dialog, because pages outside --pages must keep their layout.
            page_split::Settings::UpdateAction update;
            // Set the type explicitly, because the project default layout can be a fixed type and not auto.
            update.setLayoutType(cli.getLayout());
            if (cli.getLayout() == page_split::AUTO_LAYOUT_TYPE) {
                update.clearParams();
            }
            for (PageId const& page : allPages) {
                page_split->getSettings()->updatePage(page.imageId(), update);
            }
        }
    }
}

void
ConsoleBatch::setupDeskew(std::set<PageId> allPages)
{
    IntrusivePtr<deskew::Filter> deskew = m_ptrStages->deskewFilter();
    CommandLine const& cli = CommandLine::get();

    for (PageId const& page : allPages) {

        // DESKEW FILTER
        OrthogonalRotation rotation;
        if (cli.hasDeskewAngle() || cli.hasDeskew()) {
            double angle = 0.0;
            if (cli.hasDeskewAngle()) {
                angle = cli.getDeskewAngle();
            }
            deskew::Dependencies deps(QPolygonF(), rotation);
            deskew::Params params(angle, deps, MODE_MANUAL);
            deskew->getSettings()->setPageParams(page, params);
        }
    }

    if (cli.hasSkewDeviation()) {
        deskew->getSettings()->setMaxDeviation(cli.getSkewDeviation());
    }
}

void
ConsoleBatch::setupSelectContent(std::set<PageId> allPages)
{
    IntrusivePtr<select_content::Filter> select_content = m_ptrStages->selectContentFilter();
    CommandLine const& cli = CommandLine::get();

    for (PageId const& page : allPages) {
        select_content::Dependencies deps;

        select_content::Params params(deps);
        std::unique_ptr<select_content::Params> old_params = select_content->getSettings()->getPageParams(page);

        if (old_params.get()) {
            params = *old_params;
        }

        // SELECT CONTENT FILTER
        if (cli.hasContentRect()) {
            params.setContentRect(cli.getContentRect());
            //QRectF rect(cli.getContentRect());
            //QSizeF size_mm(rect.width(), rect.height());
            //select_content::Params params(rect, size_mm, deps, MODE_MANUAL);
        }

        params.setContentDetect(cli.isContentDetectionEnabled());
        params.setPageDetect(cli.isPageDetectionEnabled());
        params.setFineTuneCorners(cli.isFineTuningEnabled());
        if (cli.hasPageBorders()) {
            params.setPageBorders(cli.getPageBorders());
        }

        select_content->getSettings()->setPageParams(page, params);
    }

    if (cli.hasContentDeviation()) {
        select_content->getSettings()->setMaxDeviation(cli.getContentDeviation());
    }

    if (cli.hasPageDetectionBox()) {
        select_content->getSettings()->setPageDetectionBox(cli.getPageDetectionBox());
    }

    if (cli.hasPageDetectionTolerance()) {
        select_content->getSettings()->setPageDetectionTolerance(cli.getPageDetectionTolerance());
    }
}

void
ConsoleBatch::setupPageLayout(std::set<PageId> allPages)
{
    IntrusivePtr<page_layout::Filter> page_layout = m_ptrStages->pageLayoutFilter();
    CommandLine const& cli = CommandLine::get();
    QMap<QString, float> img_cache;
    // The tolerance check compares each page with every project page, also the pages outside --pages.
    std::set<PageId> const projectPages = m_ptrPages->toPageSequence(PAGE_VIEW).asPageIdSet();

    for (PageId const& page : allPages) {
        // PAGE LAYOUT FILTER
        page_layout::Alignment alignment = cli.getAlignment();
        if (cli.hasMatchLayoutTolerance()) {
            QString const path = page.imageId().filePath();
            if (!img_cache.contains(path)) {
                QImage img(path);
                img_cache[path] = float(img.width()) / float(img.height());
            }
            float imgAspectRatio = img_cache[path];
            float tolerance = cli.getMatchLayoutTolerance();
            std::vector<float> diffs;
            for (PageId const& page : projectPages) {
                ImageId pimageId = page.imageId();
                QString ppath = pimageId.filePath();
                if (!img_cache.contains(ppath)) {
                    QImage img(ppath);
                    img_cache[ppath] = float(img.width()) / float(img.height());
                }
                float pimgAspectRatio = img_cache[ppath];
                float diff = imgAspectRatio - pimgAspectRatio;
                if (diff < 0.0) {
                    diff *= -1;
                }
                diffs.push_back(diff);
            }
            unsigned bad_diffs = 0;
            for (unsigned j = 0; j < diffs.size(); j++) {
                if (diffs[j] > tolerance) {
                    bad_diffs += 1;
                }
            }
            if (bad_diffs > (diffs.size() / 2)) {
                alignment.setNull(true);
            }
        }
        if (cli.hasMargins()) {
            page_layout->getSettings()->setHardMarginsMM(page, MarginsWithAuto(cli.getMargins()));
        }
        if (cli.hasAlignment()) {
            page_layout->getSettings()->setPageAlignment(page, alignment);
        }
    }
}

void
ConsoleBatch::setupOutput(std::set<PageId> allPages)
{
    IntrusivePtr<output::Filter> output = m_ptrStages->outputFilter();
    CommandLine const& cli = CommandLine::get();

    for (PageId const& page : allPages) {

        // OUTPUT FILTER
        output::Params params(output->getSettings()->getParams(page));
        if (cli.hasOutputDpi()) {
            Dpi outputDpi = cli.getOutputDpi();
            params.setOutputDpi(outputDpi);
        }

        output::ColorParams colorParams = params.colorParams();
        if (cli.hasColorMode()) {
            colorParams.setColorMode(cli.getColorMode());
        }

        if (cli.hasWhiteMargins() || cli.hasNormalizeIllumination() ||
                cli.hasAutoLayer() || cli.hasPictureZonesLayer() || cli.hasForegroundLayer()) {
            output::ColorGrayscaleOptions cgo;
            if (cli.hasWhiteMargins()) {
                cgo.setWhiteMargins(true);
            }
            if (cli.hasNormalizeIllumination()) {
                cgo.setNormalizeIllumination(true);
            }
            if (cli.hasAutoLayer()) {
                cgo.setAutoLayerEnabled(true);
            }
            if (cli.hasPictureZonesLayer()) {
                cgo.setPictureZonesLayerEnabled(true);
            }
            if (cli.hasForegroundLayer()) {
                cgo.setForegroundLayerEnabled(true);
            }
            colorParams.setColorGrayscaleOptions(cgo);
        }

        // Start from the page options, so that the fill flags alone do not reset the other options.
        if (cli.hasFillOffcut() || cli.hasFillingColor()) {
            output::ColorGrayscaleOptions cgo(colorParams.colorGrayscaleOptions());
            if (cli.hasFillOffcut()) {
                cgo.setFillOffcut(true);
            }
            if (cli.hasFillingColor()) {
                cgo.setFillingColor(cli.getFillingColor());
            }
            colorParams.setColorGrayscaleOptions(cgo);
        }

        if (cli.hasGrayscaleFirst()) {
            QString const value = cli.getGrayscaleFirst();
            output::GrayscaleFirst mode;
            if (value == "true" || value == "1") {
                mode = output::GRAYSCALE_FIRST_ON;
            } else if (value == "false" || value == "0") {
                mode = output::GRAYSCALE_FIRST_OFF;
            } else if (value == "auto") {
                mode = output::GRAYSCALE_FIRST_AUTO;
            } else {
                // A typo must not change the option without a message.
                throw std::runtime_error("--grayscale-first must be true, false, 1, 0 or auto.");
            }
            output::ColorGrayscaleOptions cgo(colorParams.colorGrayscaleOptions());
            cgo.setGrayscaleFirst(mode);
            colorParams.setColorGrayscaleOptions(cgo);
        }

        if (cli.hasThreshold()) {
            output::BlackWhiteOptions bwo;
            bwo.setThresholdAdjustment(cli.getThreshold());
            colorParams.setBlackWhiteOptions(bwo);
        }

        params.setColorParams(colorParams);

        if (cli.hasDespeckle()) {
            params.setDespeckleLevel(cli.getDespeckleLevel());
        }

        if (cli.hasDewarping()) {
            params.setDewarpingMode(cli.getDewarpingMode());
        }
        if (cli.hasDepthPerception()) {
            params.setDepthPerception(cli.getDepthPerception());
        }

        if (cli.hasTiffCompressionBW()) {
            GlobalStaticSettings::setTiffCompressionBW(cli.getTiffCompressionBW());
        }

        if (cli.hasTiffCompressionColor()) {
            GlobalStaticSettings::setTiffCompressionColor(cli.getTiffCompressionColor());
        }

        output->getSettings()->setParams(page, params);
    }

}
