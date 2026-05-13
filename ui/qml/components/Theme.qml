// Dynamic theme constants for light/dark runtime switching.
pragma Singleton
import QtQuick

QtObject {
    property bool darkMode: true

    // Backgrounds
    readonly property color bgPrimary:        darkMode ? "#181825" : "#F5F6FA"
    readonly property color bgSecondary:      darkMode ? "#1D1F31" : "#EDF1F7"
    readonly property color bgTertiary:       darkMode ? "#2B2E46" : "#E2E8F2"
    readonly property color panelBackground:  darkMode ? "#151625" : "#EBEFF6"
    readonly property color cardBackground:   darkMode ? "#1E1E2E" : "#FFFFFF"
    readonly property color inputBackground:  darkMode ? "#171827" : "#F9FBFF"

    // Borders
    readonly property color border:      darkMode ? "#33374F" : "#D3DBE8"
    readonly property color borderLight: darkMode ? "#434865" : "#C4CFDE"

    // Text
    readonly property color textPrimary: darkMode ? "#CDD6F4" : "#2D3436"
    readonly property color textMuted:   darkMode ? "#A6ADC8" : "#636E72"

    // Accent
    readonly property color accent:      darkMode ? "#4AA3FF" : "#2D9CDB"
    readonly property color accentHover: darkMode ? "#6AB6FF" : "#238BC9"

    // Semantic
    readonly property color success:      "#2ECC71"
    readonly property color successHover: "#28B463"
    readonly property color danger:       darkMode ? "#E06C75" : "#D35454"
    readonly property color dangerHover:  darkMode ? "#CC5E67" : "#C24646"
    readonly property color warning:      darkMode ? "#E5C07B" : "#D9A441"

    // Toggle colors
    readonly property color switchOff:   darkMode ? "#4F556F" : "#C8CFDB"
    readonly property color switchThumb: "#FFFFFF"

    // Dimensions
    readonly property int radiusSmall:   6
    readonly property int radiusMedium:  8
    readonly property int radiusLarge:   12
    readonly property int spacingSmall:  8
    readonly property int spacingMedium: 12
    readonly property int spacingLarge:  16
}
