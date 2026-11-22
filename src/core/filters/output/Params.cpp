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

#include "Params.h"
#include "ColorGrayscaleOptions.h"
#include "BlackWhiteOptions.h"
#include "XmlMarshaller.h"
#include "XmlUnmarshaller.h"
#include "CommandLine.h"
#include <QDomDocument>
#include <QDomElement>
#include <QByteArray>
#include <QString>
#include "settings/ini_keys.h"

namespace output
{

Params::Params()
    :  RegenParams(), m_dpi(CommandLine::get().getDefaultOutputDpi()),
       m_blackOnWhite(true), m_blackOnWhiteSetManually(false), m_outputRotation(0)
{
    QSettings s;
    m_despeckleLevel = (DespeckleLevel) s.value(_key_output_despeckling_default_lvl, _key_output_despeckling_default_lvl_def).toUInt();
}

Params::Params(QDomElement const& el)
    :   RegenParams(),
        m_dpi(XmlUnmarshaller::dpi(el.namedItem("dpi").toElement())),
        m_distortionModel(el.namedItem("distortion-model").toElement()),
        m_depthPerception(el.attribute("depthPerception")),
        m_dewarpingMode(el.attribute("dewarpingMode")),
        m_despeckleLevel(despeckleLevelFromString(el.attribute("despeckleLevel"))),
        m_blackOnWhite(el.attribute("blackOnWhite", "1") == "1"),
        m_blackOnWhiteSetManually(el.attribute("blackOnWhiteSetManually") == "1"),
        m_outputRotation(normalizeOutputRotation(el.attribute("outputRotation").toInt()))
//        m_TiffCompression(el.attribute("tiff-compression"))

{
    // ColorParams reads the options of the other color mode too, so that a mode change keeps them.
    m_colorParams = ColorParams(el.namedItem("color-params").toElement());
}

QDomElement
Params::toXml(QDomDocument& doc, QString const& name) const
{
    XmlMarshaller marshaller(doc);

    QDomElement el(doc.createElement(name));
    el.appendChild(m_distortionModel.toXml(doc, "distortion-model"));
    el.setAttribute("depthPerception", m_depthPerception.toString());
    el.setAttribute("dewarpingMode", m_dewarpingMode.toString());
    el.setAttribute("despeckleLevel", despeckleLevelToString(m_despeckleLevel));
    // Write the new attributes only when they differ from the defaults, so that normal projects stay the same.
    if (!m_blackOnWhite) {
        el.setAttribute("blackOnWhite", "0");
    }
    if (m_blackOnWhiteSetManually) {
        el.setAttribute("blackOnWhiteSetManually", "1");
    }
    if (m_outputRotation != 0) {
        el.setAttribute("outputRotation", m_outputRotation);
    }
//    el.setAttribute("tiff-compression", m_TiffCompression);
    el.appendChild(marshaller.dpi(m_dpi, "dpi"));

    el.appendChild(m_colorParams.toXml(doc, "color-params"));

    return el;
}

int
Params::normalizeOutputRotation(int const degrees)
{
    // Only quarter turns keep the pixels exact, so any other value counts as no rotation.
    int const d = ((degrees % 360) + 360) % 360;
    return (d % 90 == 0) ? d : 0;
}

ColorParams::ColorMode
Params::parseColorMode(QString const& str)
{
    if (str == "bw") {
        return ColorParams::BLACK_AND_WHITE;
    } else if (str == "bitonal") {
        // Backwards compatibility.
        return ColorParams::BLACK_AND_WHITE;
    } else if (str == "colorOrGray") {
        return ColorParams::COLOR_GRAYSCALE;
    } else if (str == "mixed") {
        return ColorParams::MIXED;
    } else {
        return ColorParams::DefaultColorMode();
    }
}

QString
Params::formatColorMode(ColorParams::ColorMode const mode)
{
    char const* str = "";
    switch (mode) {
    case ColorParams::BLACK_AND_WHITE:
        str = "bw";
        break;
    case ColorParams::COLOR_GRAYSCALE:
        str = "colorOrGray";
        break;
    case ColorParams::MIXED:
        str = "mixed";
        break;
    }
    return QLatin1String(str);
}

void
Params::setColorParams(ColorParams const& params, ColorParamsApplyFilter const& filter)
{
    switch (filter) {
    case CopyAll:
        m_colorParams = params;
        break;
    case CopyMode: {
        if (m_colorParams.colorMode() != params.colorMode()) {
            // it's time to apply defaults
            //TRUF m_pictureShape = (PictureShape) QSettings().value("picture_zones_layer/default", output::FREE_SHAPE).toUInt();
        }
        m_colorParams.copyModeAndOptions(params);
    }
    break;

    case CopyAllThresholds: {
        m_colorParams.setBlackWhiteOptions(params.blackWhiteOptions());
    }
    break;

    case CopyThreshold: {
        BlackWhiteOptions opt = params.blackWhiteOptions();
        opt.setThresholdForegroundAdjustment(m_colorParams.blackWhiteOptions().thresholdForegroundAdjustment());
        m_colorParams.setBlackWhiteOptions(opt);
    }
    break;
    case CopyForegroundThreshold: {
        BlackWhiteOptions opt = m_colorParams.blackWhiteOptions();
        opt.setThresholdForegroundAdjustment(params.blackWhiteOptions().thresholdForegroundAdjustment());
        m_colorParams.setBlackWhiteOptions(opt);
    }
    break;
    }
}

} // namespace output
