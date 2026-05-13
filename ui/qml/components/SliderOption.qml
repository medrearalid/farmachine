import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    property alias text: label.text
    property alias from: slider.from
    property alias to: slider.to
    property real value: 0
    property alias stepSize: slider.stepSize
    property string suffix: ""
    signal valueEdited(real value)
    Layout.fillWidth: true

    Label {
        id: label
        color: Theme.textPrimary
        font { pixelSize: 11; family: "Segoe UI" }
        Layout.preferredWidth: 170
    }

    Slider {
        id: slider
        Layout.fillWidth: true
        value: root.value

        onValueChanged: {
            if (Math.abs(root.value - value) < 0.0001)
                return
            root.value = value
            root.valueEdited(value)
        }
    }

    Label {
        text: slider.value.toFixed(stepSize < 1 ? 2 : 0) + suffix
        color: Theme.textMuted
        font { pixelSize: 11; family: "Segoe UI Semibold" }
        Layout.preferredWidth: 58
        horizontalAlignment: Text.AlignRight
    }
}
