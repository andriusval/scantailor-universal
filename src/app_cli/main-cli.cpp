/*
    Scan Tailor - Interactive post-processing tool for scanned pages.
    Copyright (C) 2007-2009  Joseph Artsimovich <joseph_a@mail.ru>
    Copyright (C) 2011  Petr Kovar <pejuko@gmail.com>

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

#include <QCoreApplication>
#include "settings/ini_keys.h"
#include <QString>
#include <QStringList>
#include <QFileInfo>
#include <QJsonObject>
#include <iostream>
#include <cmath>

#include "CommandLine.h"
#include "PngMetadataLoader.h"
#include "TiffMetadataLoader.h"
#include "JpegMetadataLoader.h"
#ifdef ENABLE_OPENJPEG
#include "Jp2MetadataLoader.h"
#endif
#ifdef ENABLE_MUPDF
#include "PdfMetadataLoader.h"
#endif
#include "GenericMetadataLoader.h"
#include "ConsoleBatch.h"
#include "ImageLoader.h"
#include "imageproc/OrientationDetector.h"
#include "config.h"
#include <QFile>
#include <QImage>

static void
printRunError(QString const& message, ConsoleBatch const* batch = nullptr)
{
    QJsonObject ev;
    ev["event"] = QStringLiteral("run_end");
    ev["status"] = QStringLiteral("error");
    ev["error"] = message;
    if (batch) {
        ev["page_errors"] = batch->pageErrorCount();
        ev["warnings"] = batch->warningCount();
    }
    ConsoleBatch::printJsonLine(ev);
}

// A check of many files must not change a project, thus this mode only reads the images and prints the results.
static int
detectOrientation(QString const& list_file)
{
    using imageproc::OrientationDetector;
    using imageproc::OrientationResult;

    QFile file(list_file);
    if (!file.open(QIODevice::ReadOnly)) {
        std::cerr << "Cannot read the list file " << list_file.toLocal8Bit().constData() << std::endl;
        printRunError(QStringLiteral("cannot read the list file"));
        return 1;
    }
    QStringList const lines(QString::fromUtf8(file.readAll()).split(QLatin1Char('\n')));
    for (QString const& line : lines) {
        // A multi-page file needs the page number, thus a line can hold the path, a tab and the zero-based page.
        QStringList const parts(line.trimmed().split(QLatin1Char('\t')));
        QString const path(parts.value(0).trimmed());
        if (path.isEmpty()) {
            continue;
        }
        int const page = parts.size() > 1 ? parts[1].trimmed().toInt() : 0;
        OrientationResult result;
        int exif = -1;
        try {
            QImage const image(ImageLoader::load(path, page));
            exif = OrientationDetector::exifRotation(path);
            result = image.isNull() ? OrientationResult(0, 0.0, false, QStringLiteral("cannot load the image"))
                     : OrientationDetector::detect(image, exif);
        } catch (std::exception const& e) {
            result = OrientationResult(0, 0.0, false, QString::fromLocal8Bit(e.what()));
        }
        QJsonObject ev;
        ev["event"] = QStringLiteral("orientation");
        ev["image"] = path;
        ev["page"] = page;
        ev["orientation_detected"] = result.isDecided() ? QJsonValue(result.angle()) : QJsonValue();
        ev["confidence"] = std::round(result.confidence() * 1000.0) / 1000.0;
        ev["reason"] = result.reason();
        if (exif >= 0) {
            ev["exif_rotation"] = exif;
        }
        if (result.shapeAxis() >= 0) {
            ev["shape_axis"] = result.shapeAxis();
        }
        ConsoleBatch::printJsonLine(ev);
    }
    return 0;
}

int main(int argc, char** argv)
{
    Q_INIT_RESOURCE(core_resources);
    QCoreApplication app(argc, argv);

#ifdef _WIN32
    // Get rid of all references to Qt's installation directory.
    app.setLibraryPaths(QStringList(app.applicationDirPath()));
#endif

    // This information is used by QSettings.
    app.setApplicationName(APPLICATION_NAME);
    app.setOrganizationName(ORGANIZATION_NAME);
    app.setOrganizationDomain(ORGANIZATION_DOMAIN);
    QSettings::setDefaultFormat(QSettings::IniFormat);

    // image metadata (DPI, size, pages) for images given on the command line, as in the GUI
    PngMetadataLoader::registerMyself();
    TiffMetadataLoader::registerMyself();
    JpegMetadataLoader::registerMyself();
#ifdef ENABLE_OPENJPEG
    Jp2MetadataLoader::registerMyself();
#endif
#ifdef ENABLE_MUPDF
    PdfMetadataLoader::registerMyself();
#endif
    // the last one: it loads the whole image
    GenericMetadataLoader::registerMyself();

    // The parser can write text, thus send that text to stderr before parsing when stdout must carry only JSON lines.
    bool const json = app.arguments().contains(QStringLiteral("--json-progress"));
    if (json) {
        std::cout.rdbuf(std::cerr.rdbuf());
    }

    // parse command line arguments
    CommandLine cli(app.arguments(), false);
    CommandLine::set(cli);

    if (cli.isError()) {
        cli.printHelp();
        if (json) {
            printRunError(QStringLiteral("invalid command line"));
        }
        return 1;
    }

    // The parser gives "true" for an option without a value, and that is not a list file.
    if (cli.hasDetectOrientation() && cli.getDetectOrientationList() == "true") {
        std::cerr << "Use --detect-orientation=<list file>." << std::endl;
        if (json) {
            printRunError(QStringLiteral("--detect-orientation needs a list file"));
        }
        return 1;
    }
    if (cli.hasDetectOrientation() && !cli.hasHelp()) {
        return detectOrientation(cli.getDetectOrientationList());
    }

    if (cli.hasHelp() || cli.outputDirectory().isEmpty() || (cli.images().size() == 0 && cli.projectFile().isEmpty())) {
        cli.printHelp();
        if (json && !cli.hasHelp()) {
            printRunError(QStringLiteral("no input images or no output directory"));
        }
        return 0;
    }

    // The parser gives "true" for an option without a value, and that is not a directory.
    if (cli.renderPreviewGiven()
            && (!cli.hasRenderPreview() || cli.getRenderPreviewDirectory() == "true")) {
        std::cerr << "Use --render-preview=<directory>." << std::endl;
        return 1;
    }
    if (cli.dumpMasksGiven() && (!cli.hasDumpMasks() || cli.getDumpMasksDirectory() == "true")) {
        std::cerr << "Use --dump-masks=<directory>." << std::endl;
        if (json) {
            printRunError(QStringLiteral("--dump-masks needs a directory"));
        }
        return 1;
    }
    if (cli.hasRenderPreviewSize() && cli.getRenderPreviewSize() <= 0) {
        std::cerr << "Use --render-preview-size=<pixels> with a positive number." << std::endl;
        return 1;
    }

    if (cli.hasThreads() && cli.getThreads() < 1) {
        std::cerr << "Use --threads=<N> with N = 0 or more, or --threads=auto." << std::endl;
        return 1;
    }

    std::unique_ptr<ConsoleBatch> cbatch;

    try {
        if (!cli.projectFile().isEmpty()) {
            cbatch.reset(new ConsoleBatch(cli.projectFile()));
        } else {
            cbatch.reset(new ConsoleBatch(cli.images(), cli.outputDirectory(), cli.getLayoutDirection()));
        }
        cbatch->process();
    } catch (std::exception const& e) {
        std::cerr << e.what() << std::endl;
        if (json) {
            printRunError(QString::fromLocal8Bit(e.what()), cbatch.get());
        }
        exit(1);
    }

    // Previews come before the save, so that the saved project holds what they read, but a preview error does not stop the save.
    int preview_failures = 0;
    QString preview_error;
    if (cli.hasRenderPreview()) {
        try {
            preview_failures = cbatch->renderPreviews(cli.getRenderPreviewDirectory(), cli.getRenderPreviewSize());
        } catch (std::exception const& e) {
            std::cerr << e.what() << std::endl;
            preview_error = QString::fromLocal8Bit(e.what());
        }
    }

    // A page without masks is a warning, but a failure to make the mask directory is an error.
    int mask_failures = 0;
    QString mask_error;
    if (cli.hasDumpMasks()) {
        try {
            mask_failures = cbatch->dumpMasks(cli.getDumpMasksDirectory());
        } catch (std::exception const& e) {
            std::cerr << e.what() << std::endl;
            mask_error = QString::fromLocal8Bit(e.what());
        }
    }

    try {
        if (cli.hasOutputProject() && !cbatch->saveProject(cli.outputProjectFile()) && json) {
            printRunError(QStringLiteral("cannot write the project file"), cbatch.get());
            return 1;
        }
    } catch (std::exception const& e) {
        std::cerr << e.what() << std::endl;
        if (json) {
            printRunError(QString::fromLocal8Bit(e.what()), cbatch.get());
        }
        return 1;
    }

    bool const preview_failed = preview_failures > 0 || !preview_error.isEmpty();
    bool const run_failed = preview_failed || !mask_error.isEmpty();

    if (json) {
        // The status is "error" only when the exit code is not zero, and a warning does not change the exit code.
        QJsonObject ev;
        ev["event"] = QStringLiteral("run_end");
        ev["status"] = run_failed ? QStringLiteral("error")
                       : (cbatch->warningCount() > 0 ? QStringLiteral("warning") : QStringLiteral("ok"));
        ev["pages"] = cbatch->pageCount();
        ev["page_errors"] = cbatch->pageErrorCount();
        ev["warnings"] = cbatch->warningCount();
        if (cli.hasOutputProject()) {
            ev["project"] = QFileInfo(cli.outputProjectFile()).absoluteFilePath();
        }
        if (cli.hasRenderPreview()) {
            ev["preview_failures"] = preview_failures;
        }
        if (cli.hasDumpMasks()) {
            ev["mask_failures"] = mask_failures;
        }
        if (!mask_error.isEmpty()) {
            ev["error"] = mask_error;
        } else if (!preview_error.isEmpty()) {
            ev["error"] = preview_error;
        } else if (preview_failed) {
            ev["error"] = QStringLiteral("%1 preview(s) failed").arg(preview_failures);
        }
        ConsoleBatch::printJsonLine(ev);
    }

    return run_failed ? 1 : 0;
}
