import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    property alias text: label.text
    property bool checked: false
    signal toggled(bool checked)
    spacing: Theme.spacingMedium
    Layout.fillWidth: true
    opacity: root.enabled ? 1.0 : 0.55

    Label {
        id: label
        color: Theme.textPrimary
        font { pixelSize: 11; family: "Segoe UI" }
        Layout.fillWidth: true
    }

    ToggleSwitch {
        id: toggle
        checked: root.checked
        enabled: root.enabled
        onToggled: function(state) {
            if (root.checked === state)
                return
            root.checked = state
            root.toggled(state)
        }
    }
}
