import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card
    color: Theme.cardBackground
    radius: Theme.radiusMedium
    border.color: "transparent"
    border.width: 0

    property alias title: titleLabel.text
    property bool showTitle: title.length > 0
    default property alias content: contentCol.data

    implicitHeight: col.implicitHeight + 2 * Theme.spacingLarge
    Layout.fillWidth: true

    Column {
        id: col
        anchors.fill: parent
        anchors.margins: Theme.spacingLarge
        spacing: Theme.spacingSmall

        Label {
            id: titleLabel
            visible: card.showTitle
            color: Theme.accent
            font { pixelSize: 11; bold: true; family: "Segoe UI" }
        }

        Column {
            id: contentCol
            width: parent.width
            spacing: Theme.spacingSmall
        }
    }
}
