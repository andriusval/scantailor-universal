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

#ifndef CONSOLEBATCH_H_
#define CONSOLEBATCH_H_

#include <QString>
#include <QJsonObject>
#include <set>
#include <vector>

#include "IntrusivePtr.h"
#include "BackgroundTask.h"
#include "FilterResult.h"
#include "OutputFileNameGenerator.h"
#include "PageId.h"
#include "PageInfo.h"
#include "PageSequence.h"
#include "PageView.h"
#include "ProjectPages.h"
#include "ImageFileInfo.h"
#include "ThumbnailPixmapCache.h"
#include "OutputFileNameGenerator.h"
#include "StageSequence.h"
#include "PageSelectionAccessor.h"
#include "ProjectReader.h"

namespace output
{
class Task;
}

class LoadFileTask;

class ConsoleBatch
{
    // Member-wise copying is OK.
public:
    ConsoleBatch(
        std::vector<ImageFileInfo> const& images,
        QString                    const& output_directory,
        Qt::LayoutDirection        const  layout);
    ConsoleBatch(QString const project_file);

    void process();
    bool saveProject(QString const project_file);
    int renderPreviews(QString const& directory, int max_size);
    // Returns the number of pages without masks, and each such page also counts as a warning.
    int dumpMasks(QString const& directory);
    int pageCount() const;
    int pageErrorCount() const
    {
        return m_pageErrors;
    }
    int warningCount() const
    {
        return m_warnings;
    }

    static void printJsonLine(QJsonObject const& obj);
private:
    bool batch;
    bool debug;
    int m_pageErrors;
    int m_warnings;
    IntrusivePtr<FileNameDisambiguator> m_ptrDisambiguator;
    IntrusivePtr<ProjectPages> m_ptrPages;
    IntrusivePtr<StageSequence> m_ptrStages;
    OutputFileNameGenerator m_outFileNameGen;
    IntrusivePtr<ThumbnailPixmapCache> m_ptrThumbnailCache;
    std::unique_ptr<ProjectReader> m_ptrReader;
    bool m_hasPageFilter;
    std::set<PageId> m_filterPages;
    std::set<ImageId> m_filterImages;

    void parsePageFilter(QString const& value);
    std::set<PageId> selectedPages(PageSequence const& pages) const;

    void setupFilter(int idx, std::set<PageId> allPages);
    void setupFixOrientation(std::set<PageId> allPages);
    void setupPageSplit(std::set<PageId> allPages);
    void setupDeskew(std::set<PageId> allPages);
    void setupSelectContent(std::set<PageId> allPages);
    void setupPageLayout(std::set<PageId> allPages);
    void setupOutput(std::set<PageId> allPages);

    void checkImageFiles();

    QString stageName(int idx) const;
    QJsonObject pageResult(int idx, PageId const& page_id) const;

    bool processPage(
        int stage, PageInfo const& page, int index, int num_pages,
        IntrusivePtr<LoadFileTask> const& task, IntrusivePtr<output::Task> const& output_task);
    int processPagesInParallel(int stage, std::vector<PageInfo> const& pages, int num_threads);

    IntrusivePtr<LoadFileTask> createCompositeTask(
        PageInfo const& page,
        int const last_filter_idx,
        IntrusivePtr<output::Task>* output_task_out = nullptr
    );
};

#endif
