import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: root
    property alias text: label.text
    property var modelData: []
    property int currentIndex: -1
    readonly property string currentText: combo.currentText
    signal selectionChanged(int index)
    Layout.fillWidth: true

    Label {
        id: label
        color: Theme.textPrimary
        font { pixelSize: 11; family: "Segoe UI" }
        Layout.fillWidth: true
    }

    ComboBox {
        id: combo
        Layout.preferredWidth: 172
        model: root.modelData
        currentIndex: root.currentIndex
        font { pixelSize: 11; family: "Segoe UI" }

        onActivated: function(index) {
            if (root.currentIndex === index)
                return
            root.currentIndex = index
            root.selectionChanged(index)
        }

        contentItem: Text {
            text: combo.displayText
            color: Theme.textPrimary
            font: combo.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            leftPadding: 10
            rightPadding: 30
        }

        background: Rectangle {
            color: Theme.inputBackground
            radius: Theme.radiusSmall
            border.width: 1
            border.color: combo.hovered ? Theme.borderLight : Theme.border
        }

        popup: Popup {
            y: combo.height + 4
            width: combo.width
            implicitHeight: contentItem.implicitHeight + 8
            padding: 4

            contentItem: ListView {
                clip: true
                implicitHeight: contentHeight
                model: combo.popup.visible ? combo.delegateModel : null
                currentIndex: combo.highlightedIndex
            }

            background: Rectangle {
                color: Theme.cardBackground
                radius: Theme.radiusSmall
                border.width: 1
                border.color: Theme.border
            }
        }
    }
}
