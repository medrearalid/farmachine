import QtQuick
import QtQuick.Controls

Rectangle {
    property alias text: label.text
    property color badgeColor: Theme.textMuted

    width: row.implicitWidth + 14
    height: 20
    radius: 10
    color: Qt.rgba(badgeColor.r, badgeColor.g, badgeColor.b, 0.15)

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 4
        Rectangle { width: 5; height: 5; radius: 2.5; color: badgeColor; anchors.verticalCenter: parent.verticalCenter }
        Label { id: label; color: badgeColor; font { pixelSize: 9; bold: true; family: "Segoe UI" } }
    }
}
