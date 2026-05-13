import QtQuick
import QtQuick.Controls

Button {
    id: btn
    property color baseColor: Theme.accent
    property color hoverColor: Theme.accentHover
    property color textColor: "#ffffff"

    font { pixelSize: 12; bold: true; family: "Segoe UI Semibold" }
    contentItem: Label {
        text: btn.text
        color: btn.textColor
        font: btn.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        color: btn.down ? Qt.darker(btn.baseColor, 1.2) : btn.hovered ? btn.hoverColor : btn.baseColor
        radius: 6
        opacity: btn.enabled ? 1.0 : 0.4
        border.width: 0
        border.color: "transparent"
    }
}
