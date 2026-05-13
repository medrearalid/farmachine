import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    property alias text: label.text
    property int from: 0
    property int to: 9999
    property int value: from
    property int stepSize: 1
    property string suffix: ""
    Layout.fillWidth: true

    function clamp(v) {
        var minVal = Math.min(from, to)
        var maxVal = Math.max(from, to)
        var raw = Math.round(v)
        if (raw < minVal)
            return minVal
        if (raw > maxVal)
            return maxVal
        return raw
    }

    function increment() {
        value = clamp(value + Math.max(1, stepSize))
    }

    function decrement() {
        value = clamp(value - Math.max(1, stepSize))
    }

    onFromChanged: value = clamp(value)
    onToChanged: value = clamp(value)
    onValueChanged: {
        if (value !== clamp(value))
            value = clamp(value)
    }

    Label {
        id: label
        color: Theme.textPrimary
        font { pixelSize: 11; family: "Segoe UI" }
        Layout.fillWidth: true
    }

    Rectangle {
        id: control
        Layout.preferredWidth: 140
        Layout.minimumWidth: 130
        implicitHeight: 32
        radius: Theme.radiusMedium
        color: Theme.inputBackground
        border.width: 1
        border.color: Theme.borderLight

        RowLayout {
            anchors.fill: parent
            anchors.margins: 2
            spacing: 2

            Rectangle {
                Layout.preferredWidth: 32
                Layout.fillHeight: true
                radius: Theme.radiusSmall
                color: decArea.pressed ? Theme.borderLight : Theme.bgTertiary

                Label {
                    anchors.centerIn: parent
                    text: "−"
                    color: Theme.textPrimary
                    font { pixelSize: 16; bold: true; family: "Segoe UI" }
                }

                MouseArea {
                    id: decArea
                    anchors.fill: parent
                    onClicked: root.decrement()
                }
            }

            Label {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                text: root.value.toString() + suffix
                color: Theme.textPrimary
                font { pixelSize: 10; family: "Segoe UI Semibold" }
                elide: Text.ElideRight
            }

            Rectangle {
                Layout.preferredWidth: 32
                Layout.fillHeight: true
                radius: Theme.radiusSmall
                color: incArea.pressed ? Theme.borderLight : Theme.bgTertiary

                Label {
                    anchors.centerIn: parent
                    text: "+"
                    color: Theme.textPrimary
                    font { pixelSize: 16; bold: true; family: "Segoe UI" }
                }

                MouseArea {
                    id: incArea
                    anchors.fill: parent
                    onClicked: root.increment()
                }
            }
        }
    }
}
