import glob
import os
import pandas as pd
import numpy as np
import h5py
import xarray as xr
from datetime import datetime, timedelta
from shapely.geometry import Point
import geopandas as gpd
import concurrent.futures
import dask.array as da
from tqdm import tqdm 


path = r"/nfsscratch/Users/wndrsn"
os.chdir(path)


def colocations_main(atl_data, modis_data, time_threshold_hours=3):

    threshold_distance = 0.04

    # Create GeoDataFrame for ATL data
    geometry_atl = [Point(xy) for xy in zip(atl_data['Long0'], atl_data['Lat0'])]
    gdf_atl = gpd.GeoDataFrame(atl_data, geometry=geometry_atl)

    # Create GeoDataFrame for MODIS data
    geometry_modis = [Point(xy) for xy in zip(modis_data['Long0'], modis_data['Lat0'])]
    gdf_modis = gpd.GeoDataFrame(modis_data, geometry=geometry_modis)

    # Perform a spatial join based on proximity with a specified threshold distance
    result = gdf_atl.sjoin_nearest(gdf_modis, how='inner',max_distance = threshold_distance)

    result['Time0_left'] = pd.to_datetime(result['Time0_left'])
    result['Time0_right'] = pd.to_datetime(result['Time0_right'])

    # Adjust the filtering based on the actual column names in the result
    if 'Time0_left' in result.columns and 'Time0_right' in result.columns:
        # Filter based on time values if time_threshold_hours is provided
        if time_threshold_hours is not None:
            result = result[
                (result['Time0_left'] == result['Time0_right']) &
                (result.geometry.distance(result.geometry) <= threshold_distance) &
                (abs((result['Time0_left'] - result['Time0_right']).dt.total_seconds()) / 3600 <= time_threshold_hours)
            ]
        else:
            # Filter only based on spatial threshold if time_threshold_hours is None
            result = result[result.geometry.distance(result.geometry) <= threshold_distance]
    else:
        print("Time columns not found in the result.")

    return result

    
def XRenderMODIS(filename):
        dataset = xr.open_dataset(filename, engine="netcdf4", drop_variables = full_list)
        lat = np.array(dataset['Latitude']).flatten()
        long = np.array(dataset['Longitude']).flatten()
        time = np.array(dataset['Profile_Time']).flatten()

        lat = pd.DataFrame(lat, columns=['Lat0'])
        long = pd.DataFrame(long, columns=['Long0'])
        time = pd.DataFrame(time, columns=['Time0'])
        data = pd.concat((lat, long, time), axis=1)
        data = data['Time0'].apply(days_to_date)
        filename_df = pd.DataFrame([os.path.basename(filename)] * len(data), columns=['filename'])
        data = pd.concat([data, filename_df], axis=1)
        print(f'{filename} successfully processed!')
        dataset.close()
        return data

def HDFtoDF(filename, chunk_size=408):
    try:
        # Open the HDF file using h5py
        with h5py.File(filename, 'r') as file:
            # Access latitude, longitude, and time data using Dask
            latitude_data = da.from_array(file['profile_1']['latitude'], chunks=chunk_size)
            longitude_data = da.from_array(file['profile_1']['longitude'], chunks=chunk_size)
            delta_time_data = da.from_array(file['profile_1']['delta_time'], chunks=chunk_size)

            # Convert Dask arrays to Pandas DataFrame
            df = pd.DataFrame({
                'Lat0': latitude_data.compute(),
                'Long0': longitude_data.compute(),
                'Time0': delta_time_data.compute()
            })
            df['Time0'] = df['Time0'].apply(gps_to_datetime)
            filename_df = pd.DataFrame([os.path.basename(filename)] * len(df), columns=['filename'])
            df = pd.concat([df, filename_df], axis=1)
            print(f"{filename} successfully processed!")
        return df

    except Exception as e:
        print(f'Error reading {filename} due to {e}')



def gps_to_datetime(gps_seconds):
    gps_epoch = datetime(2018, 1, 1)
    gps_time = gps_epoch + timedelta(seconds=gps_seconds)
    return gps_time

def CALtoDayYear(yyyymmdd):
    yyyymmdd = yyyymmdd[72:76] + yyyymmdd[77:79] + yyyymmdd[80:82]
    date_object = datetime.strptime(yyyymmdd, '%Y%m%d')
    # Get the day of the year
    day_of_year = date_object.timetuple().tm_yday
    return day_of_year


def day_of_year(yyyymmdd):
    yyyymmdd = (yyyymmdd[42:50])
    # Convert the input string to a datetime object
    date_object = datetime.strptime(yyyymmdd, '%Y%m%d')

    # Get the day of the year
    day_of_year = date_object.timetuple().tm_yday

    return day_of_year

def days_to_date(input_str):
    # Split the input string to separate days and time
    days, time = str(input_str).split('days')

    # Convert the days part to a timedelta object
    duration = timedelta(days=float(days))

    # Define the reference date (1993, 1, 1)
    reference_date = datetime(1993, 1, 1)

    # Calculate the end date by adding the duration to the reference date
    end_date = reference_date + duration

    # Convert the time part to a timedelta object
    hours, minutes, seconds = map(float, time.split(':'))
    time_delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)

    # Add the time delta to the end date
    end_date_with_time = end_date + time_delta

    return end_date_with_time


def getFileTime():
    dateResultDF = pd.DataFrame([])
    CALIPSOFiles = glob.glob(os.path.join('/nfsscratch/Users/wndrsn/CALIPSOData', '**/*.hdf'), recursive=True)
    CALIPSO = pd.DataFrame({'filename':CALIPSOFiles})
    CALIPSO['day'] = CALIPSO['filename'].apply(CALtoDayYear)
    CALIPSO['year'] = CALIPSO['filename'].str.slice(72,76)
    print('MODIS Data found!')


    # Concatenate results to dateResultDF
    dateResultDF = pd.concat([dateResultDF, CALIPSO[['year', 'day','filename']]], ignore_index=True)
    atlfiles = glob.glob(os.path.join(path, '*.h5'))
    
    atl_pd = pd.DataFrame({'filename':atlfiles})
    atl_pd['day'] = atl_pd['filename'].apply(day_of_year)
    atl_pd['year'] = atl_pd['filename'].str.slice(31,35)
    dateResultDF = pd.concat((dateResultDF,atl_pd))
    print('ATL data found!')
    return dateResultDF


def process_files_for_month(file):
    if file.endswith('.h5'): return HDFtoDF(file)
    if file.endswith('.hdf'): return XRenderMODIS(file)


def main():
    years = ["2019", "2020"]
    days = ['00' + str(_) for _ in range(1,10)] + ['0' + str(_) for _ in range(10,100)] + [str(_) for _ in range(100,366)]
    print('Getting files...')
    filetimes = getFileTime()
    print('Files found!')
    filetimes['year'] = filetimes['year'].astype(str)
    filetimes['day'] = filetimes['day'].astype(str)

    # Process files for each month using ProcessPoolExecutor
    with concurrent.futures.ProcessPoolExecutor() as executor:
        # Use a list to store the futures
        modis_results = []  # Initialize an empty list before the loop
        atl_results = []
        atl_week = []
        modis_week = []
        for year in years:
            print(f'Processing year: {year}')
            i = 1
            for day in days:
                files = filetimes[(filetimes['year'] == str(year)) & ((filetimes['day'] == str(day)) | (filetimes['day'] == str(i)))]
                files = files['filename']

                modis_futures = [executor.submit(process_files_for_month, file) for file in files if file.endswith('.hdf')]    
                modis_results = [future.result() for future in tqdm(concurrent.futures.as_completed(modis_futures))]   

                atl_futures = [executor.submit(process_files_for_month, file) for file in files if file.endswith('.h5')]
                atl_results = [future.result() for future in concurrent.futures.as_completed(atl_futures)]

                # Check if each element in atl_results is a DataFrame before concatenating
                atl_results = [result for result in atl_results if isinstance(result, pd.DataFrame)]
                

                # Concatenate the valid DataFrames
                try:
                    modis_results = pd.concat(modis_results, ignore_index=True)
                    atl_results = pd.concat(atl_results, ignore_index=True)
                    # modis_results = pd.concat((modis_results, DummyTest(0)), ignore_index=True)
                    # atl_results = pd.concat((atl_results, DummyTest(0.1)), ignore_index=True)
                    atl_week.append(atl_results)
                    modis_week.append(modis_results)
                    if i%2 == 0:
                        atl_results = pd.concat(atl_week,ignore_index=True)
                        modis_results = pd.concat(modis_week,ignore_index=True)
                        colocation = pd.DataFrame(colocations_main(atl_results, modis_results))
                        colocation.to_csv(f'Aerosol colocations day {i} {year}.csv')
                        if len(colocation) < 2:
                            print('Colocation Failure')
                        print(f'{day} successfully moved to CSV!')
                        atl_week = []
                        modis_week = []
                    print(f"\nfinished processing day {i}\n")
                    i+=1
                    
                except Exception as e:
                    print(e)
                    print(f'atl_results length = {len(atl_results)} for day {i}')
                    i+=1
                    continue
                
full_list = ['Profile_ID', 'Profile_UTC_Time', 'Day_Night_Flag', 'Minimum_Laser_Energy_532', 'Column_Optical_Depth_Cloud_532', 'Column_Optical_Depth_Cloud_Uncertainty_532', 'Column_Optical_Depth_Tropospheric_Aerosols_532', 'Column_Optical_Depth_Tropospheric_Aerosols_Uncertainty_532', 'Column_Optical_Depth_Stratospheric_Aerosols_532', 'Column_Optical_Depth_Stratospheric_Aerosols_Uncertainty_532', 'Column_Optical_Depth_Tropospheric_Aerosols_1064', 'Column_Optical_Depth_Tropospheric_Aerosols_Uncertainty_1064', 'Column_Optical_Depth_Stratospheric_Aerosols_1064', 'Column_Optical_Depth_Stratospheric_Aerosols_Uncertainty_1064', 'Column_Feature_Fraction', 'Column_Integrated_Attenuated_Backscatter_532', 'Column_IAB_Cumulative_Probability', 'Tropopause_Height', 'Tropopause_Temperature', 'Temperature', 'Pressure', 'Molecular_Number_Density', 'Ozone_Number_Density', 'Relative_Humidity', 'IGBP_Surface_Type', 'Surface_Elevation_Statistics', 'Surface_Winds', 'Samples_Averaged', 'Aerosol_Layer_Fraction', 'Cloud_Layer_Fraction', 'Atmospheric_Volume_Description', 'Extinction_QC_Flag_532', 'Extinction_QC_Flag_1064', 'CAD_Score', 'Total_Backscatter_Coefficient_532', 'Total_Backscatter_Coefficient_Uncertainty_532', 'Perpendicular_Backscatter_Coefficient_532', 'Perpendicular_Backscatter_Coefficient_Uncertainty_532', 'Particulate_Depolarization_Ratio_Profile_532', 'Particulate_Depolarization_Ratio_Uncertainty_532', 'Extinction_Coefficient_532', 'Extinction_Coefficient_Uncertainty_532', 'Aerosol_Multiple_Scattering_Profile_532', 'Backscatter_Coefficient_1064', 'Backscatter_Coefficient_Uncertainty_1064', 'Extinction_Coefficient_1064', 'Extinction_Coefficient_Uncertainty_1064', 'Aerosol_Multiple_Scattering_Profile_1064', 'Surface_Top_Altitude_532', 'Surface_Base_Altitude_532', 'Surface_Integrated_Attenuated_Backscatter_532', 'Surface_532_Integrated_Depolarization_Ratio', 'Surface_532_Integrated_Attenuated_Color_Ratio', 'Surface_Detection_Flags_532', 'Surface_Detection_Confidence_532', 'Surface_Overlying_Integrated_Attenuated_Backscatter_532', 'Surface_Scaled_RMS_Background_532', 'Surface_Peak_Signal_532', 'Surface_Detections_333m_532', 'Surface_Detections_1km_532', 'Surface_Top_Altitude_1064', 'Surface_Base_Altitude_1064', 'Surface_Integrated_Attenuated_Backscatter_1064', 'Surface_1064_Integrated_Depolarization_Ratio', 'Surface_1064_Integrated_Attenuated_Color_Ratio', 'Surface_Detection_Flags_1064', 'Surface_Detection_Confidence_1064', 'Surface_Overlying_Integrated_Attenuated_Backscatter_1064', 'Surface_Scaled_RMS_Background_1064', 'Surface_Peak_Signal_1064', 'Surface_Detections_333m_1064', 'Surface_Detections_1km_1064']


main()
