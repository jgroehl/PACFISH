#
# This file extends the base PACFISH device class for the Seno (https://senomedical.com/) 
# Imagio System.  The class initializer reads in a Laser Optic Movie (LOM), which contains raw
# Photoacoustic ("Optoacoustic" in Seno terminology) and Ultrasound frames.  
#

import numpy as np
import os
import glob
import struct
import cv2

from pacfish import BaseAdapter, MetaDatum
from pacfish import MetadataAcquisitionTags
from pacfish import DeviceMetaDataCreator, DetectionElementCreator, IlluminationElementCreator

class ImagioFileConverter(BaseAdapter):

    # from OAFrameHeader.h in Seno Imagio Sw
    OAFRAMETYPE_OA = 1
    OAFRAMETYPE_US = 2
    OAFRAMETYPE_CONFIG = 3
    OAFRAMETYPE_ANNOTATION = 4
    OAFRAMETYPE_AUDIO = 5
    OAFRAMETYPE_VERSION = 6
    OAFRAMETYPE_PROBE = 7
    OAFRAMETYPE_TGC = 8
    OAFRAMETYPE_PROBE_POSITION = 9
    OAFRAME_MAGIC = 0xbee5
    OAFRAME_HEADER_SIZE = 1024
    OAFRAME_SUBHEADER_SIZE = 20
    OAFRAME_CRC_SIZE = 4 
    OAFRAME_META_LASER_INFO_OFFSET = 72
    OAFRAME_META_SAMPLE_INFO_OFFSET = 90
    OAFRAME_META_WAVELENGTH_OFFSET = 124
    OAFRAME_META_LASER_ENERGY_OFFSET =  190
    OAFRAME_DATATYPE_SHORT = 2

    # see AcquisitionInterface.h in Seno Imagio SW
    OAFRAME_DEFAULT_SAMPLES_PER_CHANNEL = 2048 

    # see ObjectBufferMetaDataDefinitions.h in Seno Imagio SW
    OAFRAME_WAVELENGTH_ALEXANDRITE = 1
    OAFRAME_WAVELENGTH_YAG = 2

    # generated via https://www.uuidgenerator.net/version4
    uuid = "a522bad9-f9a4-43b3-a8c3-80cde9e21d2e"

    meta = {}
    data = []

    """
    For the Seno Imagio system.
    """
    def __init__(self, filename):

        # parse through the entire Laser Optic Movie (.lom) file. see OAFrameHeader.h in the Imagio SW for the binary format.
        try:
            f = open(filename, "rb")
        except OSError:
            print(f"ERROR: Could not open {filename = }")
            return
        with f:
            self.meta[MetadataAcquisitionTags.ACQUISITION_WAVELENGTHS] = []
            self.meta[MetadataAcquisitionTags.PULSE_ENERGY] = []
            self.meta[MetadataAcquisitionTags.MEASUREMENT_TIMESTAMPS] = []
            self.meta[MetadataAcquisitionTags.ULTRASOUND_IMAGE_DATA] = []
            self.meta[MetadataAcquisitionTags.ULTRASOUND_IMAGE_TIMESTAMPS] = []
            self.data = []

            print(f"INFO: Reading in Seno Imagio Optoacoustic data file '{filename}'")
            while True:
                data = f.read(self.OAFRAME_HEADER_SIZE)

                if not data:
                    break

                # extracted variables (i.e. "sMagic", "iTick", etc..) line up with those defined in ObjectBufferMetaDataDefinitions.h in the Seno Imagio SW
                metaData = (sMagic, sVersion, iTick, lSize, lFrameCounter, sType, sDummy) = struct.unpack("<HHIIIhh", data[0:self.OAFRAME_SUBHEADER_SIZE]) 
                if (sMagic != self.OAFRAME_MAGIC):
                    print(f"ERROR: Unexpected magic number (read 0x{sMagic:04x}, expected 0x{self.OAFRAME_MAGIC:04x})")

                size = self.OAFRAME_HEADER_SIZE - self.OAFRAME_SUBHEADER_SIZE - self.OAFRAME_CRC_SIZE
                start = self.OAFRAME_SUBHEADER_SIZE
                end = self.OAFRAME_HEADER_SIZE - self.OAFRAME_CRC_SIZE
                headerFrameMeta = bytes(struct.unpack(str(size) + "B", data[start:end]))

                lCRC = struct.unpack("i", data[(self.OAFRAME_HEADER_SIZE - self.OAFRAME_CRC_SIZE):self.OAFRAME_HEADER_SIZE])
                frameData = f.read(lSize) 

                if (sType == self.OAFRAMETYPE_OA): # Opto-Acoustic frame

                    (sNumChans, sNumSamplesPerChannel, sDataType, lFrameCounter, sProbeID, sAcquireHardwareID, iSampleRate) = \
                        struct.unpack("<hhHIhhi", headerFrameMeta[self.OAFRAME_META_LASER_INFO_OFFSET:self.OAFRAME_META_SAMPLE_INFO_OFFSET])
                    cWavelength = struct.unpack("<B", headerFrameMeta[self.OAFRAME_META_WAVELENGTH_OFFSET:self.OAFRAME_META_WAVELENGTH_OFFSET+1])[0] # 1 = Alex, 2 = YAG
                    fLaserEnergy = struct.unpack("<f", headerFrameMeta[self.OAFRAME_META_LASER_ENERGY_OFFSET:self.OAFRAME_META_LASER_ENERGY_OFFSET+4])[0]

                    if (sDataType != self.OAFRAME_DATATYPE_SHORT):
                        print("WARNING: Data type ({sDataType = }) not as expected for frame.  Skipping to next.")
                        continue

                    print(f"INFO: Found OA frame.  {sNumSamplesPerChannel = }, {sNumChans = }, {lSize = }, {cWavelength = }, {len(frameData) = }")
           
                    # parse out the raw OA frame data
                    frameData = frameData[:sNumSamplesPerChannel*sNumChans*2] # throw away data not from the pulse (because pulses are variable length from shot to shot)
                    buf = np.frombuffer(frameData, dtype=np.int16).reshape((sNumChans, sNumSamplesPerChannel))
                    ext_buf = []
                    for idx, row in enumerate(buf): # add padding so it will write to HDF5
                        ext_buf.append(np.concatenate([row, np.zeros(self.OAFRAME_DEFAULT_SAMPLES_PER_CHANNEL - sNumSamplesPerChannel)]))
                    ext_buf = np.asarray(ext_buf)
                    self.data.append(ext_buf)
 
                    self.meta[MetadataAcquisitionTags.PULSE_ENERGY].append(fLaserEnergy / 1000) # mJ -> J
                    self.meta[MetadataAcquisitionTags.MEASUREMENT_TIMESTAMPS].append(iTick / 1000) # msec -> sec
                    if (cWavelength == self.OAFRAME_WAVELENGTH_ALEXANDRITE):
                        self.meta[MetadataAcquisitionTags.ACQUISITION_WAVELENGTHS].append(755 / 10E-9) # nanometers -> meters
                    elif (cWavelength == self.OAFRAME_WAVELENGTH_YAG):
                        self.meta[MetadataAcquisitionTags.ACQUISITION_WAVELENGTHS].append(1064 / 10E-9) # nanometers -> meters
                    else:
                        print(f"WARNING: Wavelength ({cWavelength = }) not as expected for frame.  Skipping to next")
                        continue

                elif (sType == self.OAFRAMETYPE_US): # Ultrasound frame

                    (w, h, ss) = struct.unpack("<iii", headerFrameMeta[28:40]) # width, height and sample size
                    print(f"INFO: Found US frame.  {len(frameData) = }, {w = }, {h = }, {ss = }")
                    buf = np.frombuffer(frameData[0:h*w], dtype=np.uint8).reshape(h, w)
                    self.meta[MetadataAcquisitionTags.ULTRASOUND_IMAGE_DATA].append(buf)
                    self.meta[MetadataAcquisitionTags.ULTRASOUND_IMAGE_TIMESTAMPS].append(iTick / 1000) # msec -> sec

        # required for conversion to HDF5
        for key in self.meta:
            self.meta[key] = np.asarray(self.meta[key])

        # fill out remainder of metadata
        self.meta[MetadataAcquisitionTags.ENCODING] = "raw"
        self.meta[MetadataAcquisitionTags.COMPRESSION] = "none"
        self.meta[MetadataAcquisitionTags.DATA_TYPE] = "unsigned short"
        self.meta[MetadataAcquisitionTags.DIMENSIONALITY] = "time"
        self.meta[MetadataAcquisitionTags.SIZES] = np.asarray([sNumChans, self.OAFRAME_DEFAULT_SAMPLES_PER_CHANNEL])
        self.meta[MetadataAcquisitionTags.MEASUREMENTS_PER_IMAGE] = self.OAFRAME_DEFAULT_SAMPLES_PER_CHANNEL
        self.meta[MetadataAcquisitionTags.PHOTOACOUSTIC_IMAGING_DEVICE_REFERENCE] = self.uuid
        self.meta[MetadataAcquisitionTags.UUID] = self.uuid

#        # TODO ask Bryan
#        self.meta[MetadataAcquisitionTags.AD_SAMPLING_RATE] = 0.0 
#        self.meta[MetadataAcquisitionTags.TIME_GAIN_COMPENSATION] = np.asarray([]) 
#        self.meta[MetadataAcquisitionTags.OVERALL_GAIN] = 1.0 
#        self.meta[MetadataAcquisitionTags.ELEMENT_DEPENDENT_GAIN] = np.asarray([]) 
#        self.meta[MetadataAcquisitionTags.ACOUSTIC_COUPLING_AGENT] = "gel"
#        self.meta[MetadataAcquisitionTags.SCANNING_METHOD] = ""
#        self.meta[MetadataAcquisitionTags.FREQUENCY_DOMAIN_FILTER] = np.asarray([])
#        self.meta[MetadataAcquisitionTags.SPEED_OF_SOUND] = 0.0 
#        self.meta[MetadataAcquisitionTags.REGIONS_OF_INTEREST] = {}
#        self.meta[MetadataAcquisitionTags.MEASUREMENT_SPATIAL_POSES] = np.asarray([[0],[0]])
#        self.meta[MetadataAcquisitionTags.TEMPERATURE_CONTROL] = np.asarray([])

        super().__init__()


    def generate_binary_data(self) -> np.ndarray:
        """
        The binary data is the raw time series data.
        It is internally stored as an N-dimensional numpy array.
        The binary data must be formatted the following way:

        [detectors, samples]
        e.g (128, 2048)

        Return
        ------
        np.ndarray
            A numpy array containing the binary data
        """
        return self.data

    def generate_device_meta_data(self) -> dict:
        """
        Must be implemented to define a digital twin of the photoacoustic imaging device.
        This method can be implemented using the DeviceMetaDataCreator.

        Return
        ------
        dict
            A dictionary containing all key-value pair necessary to describe a digital twin of a
            photoacoustic device.
        """

        device_creator = DeviceMetaDataCreator()

        # TODO ask Bryan
        
        # - fov
        #    is an array of six float values that describe the extent of the field of view of the device in the
        #    x1, x2, and x3 directions: [x1_start, x1_end, x2_start, x2_end, x3_start, x3_end].
        device_creator.set_general_information(
            uuid=self.uuid,
            fov=np.asarray([0, 0, 0, 0, 0, 0]))
#
#        for element_idx in range(128):
#            detection_element_creator = DetectionElementCreator()
#            detection_element_creator.set_detector_position(np.asarray([0, element_idx, 0]))
#            detection_element_creator.set_detector_geometry_type("CUBOID")
#            detection_element_creator.set_detector_geometry(np.asarray([0.0000, 0.0000, 0.0000]))
#            detection_element_creator.set_detector_orientation(np.asarray([0, 0, 1]))
#            detection_element_creator.set_frequency_response(np.asarray([np.linspace(700, 900, 100),
#                                                                         np.ones(100)]))
#            detection_element_creator.set_angular_response(np.asarray([np.linspace(700, 900, 100),
#                                                                       np.ones(100)]))
#            device_creator.add_detection_element(detection_element_creator.get_dictionary())
#
#        for wavelength in [755, 1064]: # nanometers
#            illumination_element_creator = IlluminationElementCreator()
#            illumination_element_creator.set_wavelength_range(np.asarray([wavelength, wavelength, 1]))
#            illumination_element_creator.set_illuminator_geometry(np.asarray([0, 0, 0]))
#            illumination_element_creator.set_illuminator_geometry_type("CUBOID")
#            illumination_element_creator.set_illuminator_orientation(np.asarray([0, 0, 1]))
#            illumination_element_creator.set_illuminator_position(np.asarray([0, 0, 1]))
#            illumination_element_creator.set_beam_energy_profile(np.asarray([np.linspace(700, 900, 100),
#                                                                             np.ones(100)]))
#            illumination_element_creator.set_beam_stability_profile(np.asarray([np.linspace(700, 900, 100),
#                                                                                np.ones(100)]))
#            illumination_element_creator.set_beam_intensity_profile(np.asarray([np.linspace(700, 900, 100),
#                                                                                np.ones(100)]))
#            illumination_element_creator.set_pulse_width(0.0)
#            illumination_element_creator.set_beam_divergence_angles(0.0)
#            device_creator.add_illumination_element(illumination_element_creator.get_dictionary())
#            device_creator.add_illumination_element(illumination_element_creator.get_dictionary())
#
        return device_creator.finalize_device_meta_data()  

    def set_metadata_value(self, metadatum: MetaDatum) -> object:
        """

        This method must be implemented to yield appropriate data for all MetaDatum elements in the
        MetadataTags class.

        You are given a certain meta datum nd have to return the appropriate information.

        Parameters
        ----------
        metadatum: MetaDatum
            The MetaDatum for which to return the correct data.

        Return
        ------
        object
            The data corresponding to the given MetaDatum
        """
        if metadatum in self.meta:
            return self.meta[metadatum]
        else:
            return None


